"""Deterministic business identification from page evidence.

Turns Candidate + PageEvidence + BusinessSignals into zero or more
BusinessCandidate records. Does not fetch linked websites, write SQLite,
or use AI. Classifications stay UNKNOWN when evidence is weak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

from candidates import Candidate
from classification import (
    ARTICLE_DOMAINS,
    DIRECTORY_DOMAINS,
    SOCIAL_DOMAINS,
    VIDEO_DOMAINS,
    ResultType,
    _host_matches,
)
from content_extraction import CITY_NAMES, BusinessSignals, ExtractedLink, PageEvidence
from deduplication import normalize_instagram, normalize_phone
from enrichment import EnrichedEvidence, classify_internal_page
from url_normalization import extract_domain, is_same_site, normalize_url

UNKNOWN = "UNKNOWN"
MAX_DIRECTORY_BUSINESSES = 40
LOCATION_CITIES = tuple(
    city for city in CITY_NAMES if city not in {"Bandra", "Khar", "Colaba"}
)

GENERIC_NAMES = {
    "home",
    "welcome",
    "shop",
    "shop now",
    "store",
    "stores",
    "boutique",
    "boutiques",
    "fashion",
    "women's fashion",
    "womens fashion",
    "clothing",
    "contact",
    "about",
    "about us",
    "menu",
    "search",
    "download the app",
    "download the app!",
    "get the app",
    "sign in",
    "subscribe",
    "homepage",
    "contact us",
    "contact information",
    "get in touch",
    "about us",
    "locations",
    "our stores",
    "order summary",
    "shopping cart",
    "country/region",
    "follow on instagram",
    "media coverage",
    "franchisee",
    "shipping",
    "create free account now",
    "disclaimer",
}

NAV_ANCHORS = {
    "home",
    "login",
    "log in",
    "sign up",
    "signup",
    "register",
    "search",
    "privacy",
    "privacy policy",
    "terms",
    "terms of service",
    "cookie",
    "cookies",
    "about",
    "about us",
    "contact",
    "contact us",
    "careers",
    "blog",
    "news",
    "help",
    "faq",
    "download",
    "get the app",
    "download app",
    "cart",
    "account",
    "menu",
    "more",
    "read more",
    "learn more",
    "view",
    "website",
    "map",
    "maps",
    "share",
    "pin it",
    "facebook",
    "twitter",
    "instagram",
    "pinterest",
    "youtube",
    "linkedin",
}

NAV_PATH_TOKENS = (
    "/login",
    "/signup",
    "/register",
    "/search",
    "/privacy",
    "/terms",
    "/cookie",
    "/cart",
    "/account",
    "/careers",
    "/help",
    "/faq",
    "/blog",
)

SKIP_EXTRACT_DOMAINS = (
    DIRECTORY_DOMAINS
    | SOCIAL_DOMAINS
    | VIDEO_DOMAINS
    | ARTICLE_DOMAINS
    | {
        "google.com",
        "google.co.in",
        "maps.apple.com",
        "apple.com",
        "play.google.com",
        "apps.apple.com",
        "wikipedia.org",
        "youtu.be",
        "wa.me",
        "whatsapp.com",
        "api.whatsapp.com",
        "bit.ly",
        "t.co",
    }
)

CHROME_NAME_RE = re.compile(
    r"\b(cart|checkout|order summary|shopping cart|item added|added to (?:your )?cart|"
    r"my account|please login|wishlist|order confirmation|login|register)\b",
    re.IGNORECASE,
)
ROUNDUP_TITLE_RE = re.compile(
    r"\b(best|top|coolest)\b.{0,40}\b(boutique|boutiques|stores?)\b",
    re.IGNORECASE,
)
BOUTIQUE_PHRASE_RE = re.compile(
    r"\b(fashion\s+boutique|clothing\s+boutique|designer\s+boutique|"
    r"women'?s\s+boutique|ladies\s+boutique|boutique)\b",
    re.IGNORECASE,
)
STORE_PHRASE_RE = re.compile(
    r"\b(fashion\s+store|clothing\s+store|designer\s+store|apparel)\b",
    re.IGNORECASE,
)
MULTI_DESIGNER_RE = re.compile(r"\bmulti[\s\-]?designer\b", re.IGNORECASE)
MARKETPLACE_RE = re.compile(
    r"\b(marketplace|shop\s+from\s+multiple\s+sellers)\b", re.IGNORECASE
)
NON_TARGET_RE = re.compile(
    r"\b(real\s+estate|hotel|restaurant|salon|spa\b|travel\s+agency|"
    r"shopping\s+mall|news\s+magazine)\b",
    re.IGNORECASE,
)
WOMEN_HIGH_RE = re.compile(
    r"\b(women'?s\s+(clothing|fashion|wear|boutique|apparel|store)|"
    r"womenswear|ladies\s+(clothing|wear|boutique|garments)|"
    r"women\s+dresses|women\s+tops|women\s+apparel)\b",
    re.IGNORECASE,
)
WOMEN_MEDIUM_RE = re.compile(
    r"\b(dresses|tops|skirts|sarees|sari|lehengas?|ethnic\s+wear|"
    r"kurtis?|women'?s\s+collections?)\b",
    re.IGNORECASE,
)
WOMEN_NEGATIVE_RE = re.compile(
    r"\b(men'?s\s+only|menswear\s+only|kids\s+only|children'?s\s+clothing|"
    r"jewellery\s+only|jewelry\s+only|beauty\s+only|home\s+decor)\b",
    re.IGNORECASE,
)
PHYSICAL_YES_RE = re.compile(
    r"\b(visit\s+us|showroom|store\s+locator|our\s+stores?|physical\s+store|"
    r"boutique\s+address|walk[\s\-]?in|visit\s+the\s+store|flagship\s+store)\b",
    re.IGNORECASE,
)
PHYSICAL_NO_RE = re.compile(
    r"\b(online[\s\-]?only|online[\s\-]?exclusive|no\s+physical\s+store)\b",
    re.IGNORECASE,
)
PINCODE_RE = re.compile(r"\b[1-9]\d{5}\b")


class BusinessType(str, Enum):
    BOUTIQUE = "BOUTIQUE"
    DESIGNER = "DESIGNER"
    MULTI_DESIGNER = "MULTI_DESIGNER"
    RETAILER = "RETAILER"
    MARKETPLACE = "MARKETPLACE"
    BRAND = "BRAND"
    UNKNOWN = "UNKNOWN"


class Relevance(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class PhysicalStore(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class BusinessCandidate:
    """A possible business extracted from one page. Not a confirmed boutique."""

    business_name: str
    website: str | None
    instagram: str | None
    facebook: str | None
    whatsapp: str | None
    phone: str | None
    email: str | None
    address: str | None
    city: str
    source_url: str
    source_type: str
    business_type: BusinessType
    fashion_relevance: Relevance
    women_fashion_relevance: Relevance
    physical_store: PhysicalStore
    evidence: dict
    confidence: Confidence
    extra_phones: list[str] = field(default_factory=list)
    extra_emails: list[str] = field(default_factory=list)


def identify_business_candidates(
    candidate: Candidate,
    page_evidence: PageEvidence,
    business_signals: BusinessSignals,
    enriched: EnrichedEvidence | None = None,
) -> list[BusinessCandidate]:
    """Return zero or more businesses for one discovery candidate.

    ``enriched`` is optional combined homepage + Contact/Store/About evidence.
    """
    result_type = candidate.result_type
    if result_type is ResultType.DIRECTORY:
        rows = extract_businesses_from_directory(
            candidate, page_evidence, business_signals
        )
    elif result_type is ResultType.ARTICLE:
        rows = extract_businesses_from_article(
            candidate, page_evidence, business_signals
        )
    elif result_type is ResultType.SOCIAL:
        rows = identify_business_from_social(
            candidate, page_evidence, business_signals
        )
    elif result_type is ResultType.VIDEO:
        rows = identify_business_from_video(
            candidate, page_evidence, business_signals
        )
    else:
        rows = identify_business_from_page(
            candidate, page_evidence, business_signals, enriched=enriched
        )
    return merge_in_memory_duplicates(rows)


def identify_business_from_page(
    candidate: Candidate,
    page_evidence: PageEvidence,
    business_signals: BusinessSignals,
    enriched: EnrichedEvidence | None = None,
) -> list[BusinessCandidate]:
    """Identify the website owner, or listed shops if the page is a roundup."""
    homepage_title = (page_evidence.title or candidate.title or "").strip()
    if enriched is not None and enriched.pages:
        page_evidence = enriched.as_page_evidence()
        business_signals = enriched.combined_signals()
        home = _homepage_from_enriched(enriched)
        if home and home.title:
            homepage_title = home.title.strip()
    if _is_roundup_title(homepage_title) or _is_roundup_title(candidate.title):
        listed = _businesses_from_external_links(
            candidate,
            page_evidence,
            source_type="WEBSITE",
            extra_signals=["roundup_page", "external_business_link"],
        )
        if listed:
            return listed
        return [
            _owner_candidate(
                candidate,
                page_evidence,
                business_signals,
                name=UNKNOWN,
                extra_signals=["roundup_page", "publisher_not_boutique"],
                enriched=enriched,
            )
        ]
    if enriched is not None and enriched.pages:
        name, name_source = _website_business_name_enriched(candidate, enriched)
    else:
        name, name_source = _website_business_name(candidate, page_evidence)
    extra = ["direct_website"]
    if enriched is not None and enriched.pages:
        extra.append("enriched")
    return [
        _owner_candidate(
            candidate,
            page_evidence,
            business_signals,
            name=name,
            name_source=name_source,
            extra_signals=extra,
            enriched=enriched,
        )
    ]


def extract_businesses_from_directory(
    candidate: Candidate,
    page_evidence: PageEvidence,
    business_signals: BusinessSignals,
) -> list[BusinessCandidate]:
    """Extract linked businesses. The directory itself is not a boutique."""
    return _businesses_from_external_links(
        candidate,
        page_evidence,
        source_type="DIRECTORY",
        extra_signals=["external_business_link"],
    )


def extract_businesses_from_article(
    candidate: Candidate,
    page_evidence: PageEvidence,
    business_signals: BusinessSignals,
) -> list[BusinessCandidate]:
    """Extract businesses named/linked in an article. Publisher is skipped."""
    from_links = _businesses_from_external_links(
        candidate,
        page_evidence,
        source_type="ARTICLE",
        extra_signals=["article_business_link"],
    )
    from_headings = _businesses_from_article_headings(
        candidate, page_evidence, already=from_links
    )
    return from_links + from_headings


def identify_business_from_social(
    candidate: Candidate,
    page_evidence: PageEvidence,
    business_signals: BusinessSignals,
) -> list[BusinessCandidate]:
    """Keep the profile URL. Do not infer boutique from the username."""
    url = candidate.normalized_url or candidate.url
    title = (page_evidence.title or candidate.title or "").strip()
    name, name_source = _social_profile_name(title, url)
    instagram = url if "instagram.com" in url.lower() else _pick_instagram(business_signals)
    facebook = (
        url
        if "facebook.com" in url.lower() or "fb.com" in url.lower()
        else _pick_facebook(business_signals)
    )
    confidence = Confidence.MEDIUM if name != UNKNOWN else Confidence.LOW
    return [
        BusinessCandidate(
            business_name=name,
            website=None,
            instagram=instagram,
            facebook=facebook,
            whatsapp=_first(business_signals.whatsapp_urls),
            phone=None,
            email=None,
            address=None,
            city=UNKNOWN,
            source_url=url,
            source_type="SOCIAL",
            business_type=BusinessType.UNKNOWN,
            fashion_relevance=Relevance.UNKNOWN,
            women_fashion_relevance=Relevance.UNKNOWN,
            physical_store=PhysicalStore.UNKNOWN,
            evidence={
                "source_url": url,
                "source_type": "SOCIAL",
                "name_source": name_source,
                "signals": ["social_profile"],
            },
            confidence=confidence,
        )
    ]


def identify_business_from_video(
    candidate: Candidate,
    page_evidence: PageEvidence,
    business_signals: BusinessSignals,
) -> list[BusinessCandidate]:
    """Use title/description only. Do not download video or transcripts."""
    title = (page_evidence.title or candidate.title or "").strip()
    description = page_evidence.meta_description or ""
    name, name_source = _explicit_named_business(title, description)
    if name == UNKNOWN:
        return []
    return [
        BusinessCandidate(
            business_name=name,
            website=None,
            instagram=_pick_instagram(business_signals),
            facebook=_pick_facebook(business_signals),
            whatsapp=_first(business_signals.whatsapp_urls),
            phone=None,
            email=None,
            address=None,
            city=_city_from_title(title),
            source_url=candidate.normalized_url or candidate.url,
            source_type="VIDEO",
            business_type=BusinessType.UNKNOWN,
            fashion_relevance=Relevance.UNKNOWN,
            women_fashion_relevance=Relevance.UNKNOWN,
            physical_store=PhysicalStore.UNKNOWN,
            evidence={
                "source_url": candidate.normalized_url or candidate.url,
                "source_type": "VIDEO",
                "name_source": name_source,
                "signals": ["explicit_name_in_title_or_description"],
            },
            confidence=Confidence.LOW,
        )
    ]


def merge_in_memory_duplicates(
    rows: list[BusinessCandidate],
) -> list[BusinessCandidate]:
    """Merge obvious duplicates in this run only. Not historical SQLite."""
    grouped: dict[tuple[str, str], BusinessCandidate] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = _duplicate_key(row) or ("row", str(len(order)))
        if key not in grouped:
            grouped[key] = row
            order.append(key)
        else:
            grouped[key] = _merge_pair(grouped[key], row)
    return [grouped[key] for key in order]


def _duplicate_key(row: BusinessCandidate) -> tuple[str, str] | None:
    domain = extract_domain(row.website) if row.website else None
    if domain:
        return ("website", domain)
    instagram = normalize_instagram(row.instagram)
    if instagram:
        return ("instagram", instagram)
    phone = normalize_phone(row.phone)
    if phone:
        return ("phone", phone)
    name = (row.business_name or "").strip().lower()
    city = (row.city or "").strip().lower()
    if name and name != UNKNOWN.lower() and city and city != UNKNOWN.lower():
        return ("name_city", f"{name}|{city}")
    return None


def _merge_pair(
    left: BusinessCandidate, right: BusinessCandidate
) -> BusinessCandidate:
    evidence = dict(left.evidence)
    extra_sources = list(evidence.get("merged_source_urls") or [])
    extra_sources.append(right.source_url)
    extra_sources.extend((right.evidence or {}).get("merged_source_urls") or [])
    evidence["merged_source_urls"] = list(dict.fromkeys(extra_sources))
    merged_evidence = list(evidence.get("merged_evidence") or [])
    merged_evidence.append(dict(right.evidence or {}))
    evidence["merged_evidence"] = merged_evidence
    left_signals = list(evidence.get("signals") or [])
    right_signals = list((right.evidence or {}).get("signals") or [])
    evidence["signals"] = list(dict.fromkeys(left_signals + right_signals))
    phones = _unique_optional(
        [left.phone, *left.extra_phones, right.phone, *right.extra_phones]
    )
    emails = _unique_optional(
        [left.email, *left.extra_emails, right.email, *right.extra_emails]
    )
    return BusinessCandidate(
        business_name=_prefer_known(left.business_name, right.business_name),
        website=left.website or right.website,
        instagram=left.instagram or right.instagram,
        facebook=left.facebook or right.facebook,
        whatsapp=left.whatsapp or right.whatsapp,
        phone=_first(phones),
        email=_first(emails),
        address=left.address or right.address,
        city=_prefer_known(left.city, right.city),
        source_url=left.source_url,
        source_type=left.source_type,
        business_type=_prefer_enum(
            left.business_type, right.business_type, BusinessType.UNKNOWN
        ),
        fashion_relevance=_prefer_enum(
            left.fashion_relevance, right.fashion_relevance, Relevance.UNKNOWN
        ),
        women_fashion_relevance=_prefer_enum(
            left.women_fashion_relevance,
            right.women_fashion_relevance,
            Relevance.UNKNOWN,
        ),
        physical_store=_prefer_enum(
            left.physical_store, right.physical_store, PhysicalStore.UNKNOWN
        ),
        evidence=evidence,
        confidence=_higher_confidence(left.confidence, right.confidence),
        extra_phones=phones[1:],
        extra_emails=emails[1:],
    )


def _owner_candidate(
    candidate: Candidate,
    page_evidence: PageEvidence,
    business_signals: BusinessSignals,
    *,
    name: str,
    name_source: str = "none",
    extra_signals: list[str] | None = None,
    enriched: EnrichedEvidence | None = None,
) -> BusinessCandidate:
    haystack = _haystack(candidate, page_evidence)
    website = page_evidence.final_url or candidate.normalized_url or candidate.url
    business_type, type_signals = _classify_business_type(haystack, name)
    fashion = _fashion_relevance(haystack, business_type)
    women = _women_fashion_relevance(haystack)
    physical = _physical_store(haystack, business_signals)
    address = _first(business_signals.address_candidates)
    city = _city_for_owner(
        page_evidence,
        business_signals,
        physical,
        primary_address=address,
    )
    instagram = _pick_instagram(business_signals)
    facebook = _pick_facebook(business_signals)
    email = _first(business_signals.emails)
    phone = _first(business_signals.phones)
    signals = list(extra_signals or []) + type_signals
    if name != UNKNOWN:
        signals.append("business_name")
    if instagram or facebook or phone or email:
        signals.append("contact")
    if physical is PhysicalStore.YES:
        signals.append("physical_store")
    confidence = _confidence(
        name=name,
        business_type=business_type,
        fashion=fashion,
        has_contact=bool(instagram or facebook or phone or email or address),
        physical=physical,
        has_website=bool(website),
    )
    return BusinessCandidate(
        business_name=name,
        website=normalize_url(website) or website,
        instagram=instagram,
        facebook=facebook,
        whatsapp=_first(business_signals.whatsapp_urls),
        phone=phone,
        email=email,
        address=address,
        city=city,
        source_url=candidate.normalized_url or candidate.url,
        source_type=candidate.result_type.value,
        business_type=business_type,
        fashion_relevance=fashion,
        women_fashion_relevance=women,
        physical_store=physical,
        evidence={
            "source_url": candidate.normalized_url or candidate.url,
            "source_type": candidate.result_type.value,
            "name_source": name_source,
            "signals": signals,
            **(_enrichment_evidence(enriched) if enriched is not None else {}),
        },
        confidence=confidence,
        extra_phones=list(business_signals.phones[1:]),
        extra_emails=list(business_signals.emails[1:]),
    )


def _website_business_name(
    candidate: Candidate, page_evidence: PageEvidence
) -> tuple[str, str]:
    title = (page_evidence.title or candidate.title or "").strip()
    for raw, source in (
        (title, "page_title"),
        (_first(page_evidence.headings), "heading"),
    ):
        cleaned = _clean_name(raw or "")
        if cleaned != UNKNOWN:
            return cleaned, source
    return UNKNOWN, "none"


def _homepage_from_enriched(enriched: EnrichedEvidence) -> PageEvidence | None:
    for page in enriched.pages:
        role = classify_internal_page(
            page.final_url or page.source_url, root_url=enriched.root_url
        )
        if role == "homepage":
            return page
    return enriched.pages[0] if enriched.pages else None


def _website_business_name_enriched(
    candidate: Candidate, enriched: EnrichedEvidence
) -> tuple[str, str]:
    """Prefer About, then homepage, then contact/store titles."""
    buckets: dict[str, list[PageEvidence]] = {
        "about": [],
        "homepage": [],
        "contact": [],
        "location": [],
        "other": [],
    }
    for page in enriched.pages:
        role = classify_internal_page(
            page.final_url or page.source_url, root_url=enriched.root_url
        )
        buckets.setdefault(role, []).append(page)
    ordered: list[tuple[PageEvidence, str]] = []
    for role, source in (
        ("about", "about_page"),
        ("homepage", "homepage"),
        ("contact", "contact_page"),
        ("location", "store_page"),
    ):
        for page in buckets.get(role, []):
            ordered.append((page, source))
    for page, source in ordered:
        for raw, kind in (
            (page.title or "", f"{source}_title"),
            (_first(page.headings) or "", f"{source}_heading"),
        ):
            cleaned = _clean_contextual_name(raw, source)
            if cleaned != UNKNOWN:
                return cleaned, kind
    return _website_business_name(candidate, enriched.as_page_evidence())


def _enrichment_evidence(enriched: EnrichedEvidence) -> dict:
    sources = enriched.address_sources()
    return {
        "enrichment": {
            "successful_urls": list(enriched.successful_urls),
            "failed_urls": list(enriched.failed_urls),
            "selected_urls": list(enriched.selected_urls),
            "selected_url_sources": list(enriched.selected_url_sources)[:8],
            "sitemap_discovered": enriched.sitemap_discovered,
            "sitemap_source": enriched.sitemap_source,
            "address_sources": sources[:8],
        }
    }


def _clean_name(raw: str) -> str:
    text = re.sub(r"\s+", " ", raw).strip()
    if not text:
        return UNKNOWN
    if _is_roundup_title(text) or _looks_generic(text):
        return UNKNOWN
    parts = re.split(r"\s*[-–—|]\s*", text, maxsplit=1)
    if len(parts) == 2:
        left = parts[0].strip()
        if left and not _looks_generic(left) and not _is_roundup_title(left) and len(left.split()) <= 6:
            text = left
    text = re.sub(r"\s*[\(\[]@[^)\]]+[\)\]]", "", text).strip()
    if _looks_generic(text) or _is_roundup_title(text) or len(text) < 2:
        return UNKNOWN
    if len(text) > 80:
        return UNKNOWN
    if len(text.split()) > 8:
        return UNKNOWN
    return text


def _clean_contextual_name(raw: str, source: str) -> str:
    """Remove page-label chrome only when the page role supports it."""
    text = raw
    if source == "about_page":
        text = re.sub(r"^\s*about(?:\s+us)?\s*[:\-–—]?\s+", "", text, flags=re.IGNORECASE)
        if ":" in text:
            left = text.split(":", 1)[0].strip()
            if 1 <= len(left.split()) <= 6 and _clean_name(left) != UNKNOWN:
                text = left
    elif source == "contact_page":
        text = re.sub(r"^\s*contact(?:\s+us)?\s*[:\-–—]?\s+", "", text, flags=re.IGNORECASE)
    return _clean_name(text)


def _looks_generic(text: str) -> bool:
    lowered = text.strip().lower().rstrip("!.")
    if lowered in GENERIC_NAMES:
        return True
    if CHROME_NAME_RE.search(lowered):
        return True
    if re.fullmatch(r"women'?s fashion", lowered):
        return True
    if re.search(r"\bboutiques in\b", lowered):
        return True
    if "download" in lowered and "app" in lowered:
        return True
    if "follow" in lowered and "instagram" in lowered:
        return True
    if re.fullmatch(r"women'?s\s+[\w\s-]*clothing\s+online", lowered):
        return True
    if lowered.startswith(("http://", "https://", "www.")):
        return True
    if re.match(r"^https?://", text.strip(), re.IGNORECASE):
        return True
    return False


def _is_roundup_title(text: str) -> bool:
    if not text:
        return False
    if ROUNDUP_TITLE_RE.search(text):
        return True
    return bool(
        re.search(r"\b\d+\s+best\b", text, re.IGNORECASE)
        or re.search(r"\bboutiques?\s+in\s+[A-Za-z]", text, re.IGNORECASE)
    )


def _classify_business_type(
    haystack: str, name: str
) -> tuple[BusinessType, list[str]]:
    signals: list[str] = []
    if NON_TARGET_RE.search(haystack) and not BOUTIQUE_PHRASE_RE.search(haystack):
        return BusinessType.UNKNOWN, ["non_target_keyword"]
    if MARKETPLACE_RE.search(haystack):
        return BusinessType.MARKETPLACE, ["marketplace_keyword"]
    boutique_hits = len(BOUTIQUE_PHRASE_RE.findall(haystack))
    store_hits = len(STORE_PHRASE_RE.findall(haystack))
    if MULTI_DESIGNER_RE.search(haystack) and (boutique_hits or store_hits):
        return BusinessType.MULTI_DESIGNER, ["multi_designer"]
    if boutique_hits >= 2 or (
        boutique_hits >= 1 and (store_hits or _has_phrase(haystack, "designer"))
    ):
        signals.append("boutique_phrase")
        return BusinessType.BOUTIQUE, signals
    if boutique_hits == 1 and name != UNKNOWN:
        signals.append("boutique_phrase")
        return BusinessType.BOUTIQUE, signals
    if _has_phrase(haystack, "designer") and store_hits:
        return BusinessType.DESIGNER, ["designer_store"]
    if store_hits >= 2:
        return BusinessType.RETAILER, ["clothing_store"]
    if _has_phrase(haystack, "brand") and store_hits:
        return BusinessType.BRAND, ["brand_store"]
    return BusinessType.UNKNOWN, []


def _fashion_relevance(haystack: str, business_type: BusinessType) -> Relevance:
    if business_type in {
        BusinessType.BOUTIQUE,
        BusinessType.DESIGNER,
        BusinessType.MULTI_DESIGNER,
    }:
        return Relevance.HIGH
    if BOUTIQUE_PHRASE_RE.search(haystack):
        return Relevance.HIGH
    if STORE_PHRASE_RE.search(haystack):
        return Relevance.MEDIUM
    fashion_count = len(re.findall(r"\bfashion\b", haystack, re.IGNORECASE))
    clothing_count = len(
        re.findall(r"\b(clothing|apparel|wear)\b", haystack, re.IGNORECASE)
    )
    if fashion_count >= 3 and clothing_count:
        return Relevance.MEDIUM
    return Relevance.UNKNOWN


def _women_fashion_relevance(haystack: str) -> Relevance:
    if WOMEN_NEGATIVE_RE.search(haystack) and not WOMEN_HIGH_RE.search(haystack):
        return Relevance.LOW
    if WOMEN_HIGH_RE.search(haystack):
        return Relevance.HIGH
    medium_hits = WOMEN_MEDIUM_RE.findall(haystack)
    if len(medium_hits) >= 2:
        return Relevance.MEDIUM
    return Relevance.UNKNOWN


def _physical_store(haystack: str, signals: BusinessSignals) -> PhysicalStore:
    if PHYSICAL_NO_RE.search(haystack):
        return PhysicalStore.NO
    has_address = bool(signals.address_candidates)
    has_pin = any(PINCODE_RE.search(item) for item in signals.address_candidates)
    has_phrase = bool(PHYSICAL_YES_RE.search(haystack))
    if has_address and has_pin:
        return PhysicalStore.YES
    if has_phrase and has_address:
        return PhysicalStore.YES
    return PhysicalStore.UNKNOWN


def _city_for_owner(
    evidence: PageEvidence,
    signals: BusinessSignals,
    physical: PhysicalStore,
    *,
    primary_address: str | None,
) -> str:
    address_cities: list[str] = []
    for snippet in signals.address_candidates:
        address_cities.extend(_cities_in_text(snippet))
    unique_address = list(dict.fromkeys(address_cities))
    if len(unique_address) > 1:
        return UNKNOWN
    primary_cities = _cities_in_text(primary_address or "")
    if len(primary_cities) == 1:
        if not unique_address or primary_cities[0] == unique_address[0]:
            return primary_cities[0]
    if signals.address_candidates:
        # Do not assign a city from a different address than the one retained.
        return UNKNOWN
    title_cities = _cities_in_text(evidence.title or "")
    if len(title_cities) == 1 and physical is PhysicalStore.YES:
        return title_cities[0]
    return UNKNOWN


def _city_from_title(title: str) -> str:
    cities = _cities_in_text(title)
    if len(cities) == 1:
        return cities[0]
    return UNKNOWN


def _cities_in_text(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for city in sorted(LOCATION_CITIES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(city)}\b", text, re.IGNORECASE):
            found.append(city)
    return found


def _confidence(
    *,
    name: str,
    business_type: BusinessType,
    fashion: Relevance,
    has_contact: bool,
    physical: PhysicalStore,
    has_website: bool,
) -> Confidence:
    named = name != UNKNOWN
    typed = business_type is not BusinessType.UNKNOWN
    fashion_ok = fashion in {Relevance.HIGH, Relevance.MEDIUM}
    if named and (typed or fashion_ok) and (
        has_contact or physical is PhysicalStore.YES or has_website
    ):
        if typed and (has_contact or physical is PhysicalStore.YES):
            return Confidence.HIGH
        return Confidence.MEDIUM
    if named:
        return Confidence.MEDIUM
    return Confidence.LOW


def _businesses_from_external_links(
    candidate: Candidate,
    page_evidence: PageEvidence,
    *,
    source_type: str,
    extra_signals: list[str],
) -> list[BusinessCandidate]:
    source_domain = extract_domain(
        page_evidence.final_url or candidate.normalized_url
    )
    rows: list[BusinessCandidate] = []
    for link in page_evidence.links:
        if len(rows) >= MAX_DIRECTORY_BUSINESSES:
            break
        if not _usable_business_link(link, source_domain):
            continue
        website = link.normalized_url or normalize_url(link.url) or link.url
        domain = extract_domain(website)
        if not domain:
            continue
        name = _clean_name(link.anchor_text)
        if name == UNKNOWN:
            continue
        rows.append(
            BusinessCandidate(
                business_name=name,
                website=website,
                instagram=None,
                facebook=None,
                whatsapp=None,
                phone=None,
                email=None,
                address=None,
                city=UNKNOWN,
                source_url=candidate.normalized_url or candidate.url,
                source_type=source_type,
                business_type=BusinessType.UNKNOWN,
                fashion_relevance=Relevance.UNKNOWN,
                women_fashion_relevance=Relevance.UNKNOWN,
                physical_store=PhysicalStore.UNKNOWN,
                evidence={
                    "source_url": candidate.normalized_url or candidate.url,
                    "source_type": source_type,
                    "name_source": "anchor_text",
                    "signals": list(extra_signals),
                    "anchor_text": link.anchor_text,
                    "linked_url": website,
                },
                confidence=Confidence.MEDIUM,
            )
        )
    return rows


def _usable_business_link(
    link: ExtractedLink, source_domain: str | None
) -> bool:
    if not link.external:
        return False
    target = link.normalized_url or link.url
    domain = extract_domain(target)
    if not domain or (source_domain and is_same_site(source_domain, domain)):
        return False
    if _host_matches(domain, SKIP_EXTRACT_DOMAINS):
        return False
    anchor = (link.anchor_text or "").strip().lower()
    if not anchor or anchor in NAV_ANCHORS:
        return False
    if len(anchor) < 3:
        return False
    path = urlparse(target).path.lower()
    if any(token in path for token in NAV_PATH_TOKENS):
        return False
    return True


def _businesses_from_article_headings(
    candidate: Candidate,
    page_evidence: PageEvidence,
    *,
    already: list[BusinessCandidate],
) -> list[BusinessCandidate]:
    existing = {row.business_name.lower() for row in already}
    rows: list[BusinessCandidate] = []
    for heading in page_evidence.headings:
        name = _clean_name(heading)
        if name == UNKNOWN or name.lower() in existing:
            continue
        if _looks_generic(heading) or _is_roundup_title(heading):
            continue
        if len(name.split()) > 6:
            continue
        existing.add(name.lower())
        rows.append(
            BusinessCandidate(
                business_name=name,
                website=None,
                instagram=None,
                facebook=None,
                whatsapp=None,
                phone=None,
                email=None,
                address=None,
                city=UNKNOWN,
                source_url=candidate.normalized_url or candidate.url,
                source_type="ARTICLE",
                business_type=BusinessType.UNKNOWN,
                fashion_relevance=Relevance.UNKNOWN,
                women_fashion_relevance=Relevance.UNKNOWN,
                physical_store=PhysicalStore.UNKNOWN,
                evidence={
                    "source_url": candidate.normalized_url or candidate.url,
                    "source_type": "ARTICLE",
                    "name_source": "heading",
                    "signals": ["article_heading"],
                },
                confidence=Confidence.LOW,
            )
        )
    return rows


def _social_profile_name(title: str, url: str) -> tuple[str, str]:
    cleaned = _clean_name(title)
    if cleaned != UNKNOWN:
        return cleaned, "title"
    path = urlparse(url if "://" in url else f"https://{url}").path.strip("/")
    username = path.split("/")[0] if path else ""
    if username.lower() in {"p", "reel", "reels", "stories", "explore", "watch"}:
        return UNKNOWN, "none"
    return UNKNOWN, "none"


def _explicit_named_business(title: str, description: str) -> tuple[str, str]:
    for source, text in (("title", title), ("description", description)):
        match = re.search(
            r"\b(?:at|visit(?:ing)?)\s+([A-Z][\w'&+\-]+(?:\s+[A-Z][\w'&+\-]+){0,4})",
            text,
        )
        if match:
            name = _clean_name(match.group(1))
            if name != UNKNOWN:
                return name, source
    return UNKNOWN, "none"


def _pick_instagram(signals: BusinessSignals) -> str | None:
    for url in signals.social_urls:
        if "instagram.com" in url.lower():
            return url
    return None


def _pick_facebook(signals: BusinessSignals) -> str | None:
    for url in signals.social_urls:
        if "facebook.com" in url.lower() or "fb.com" in url.lower():
            return url
    return None


def _haystack(candidate: Candidate, evidence: PageEvidence) -> str:
    parts = [
        candidate.title,
        evidence.title,
        evidence.meta_description,
        " ".join(evidence.headings),
        evidence.text[:24000],
    ]
    return " ".join(part for part in parts if part)


def _first(values: list[str]) -> str | None:
    return values[0] if values else None


def _unique_optional(values: list[str | None]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _has_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", text, re.IGNORECASE) is not None


def _prefer_known(left: str, right: str) -> str:
    if left and left != UNKNOWN:
        return left
    if right and right != UNKNOWN:
        return right
    return left or right or UNKNOWN


def _prefer_enum(left, right, unknown):
    if left is not unknown:
        return left
    return right


def _higher_confidence(left: Confidence, right: Confidence) -> Confidence:
    rank = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
    return left if rank[left] >= rank[right] else right

