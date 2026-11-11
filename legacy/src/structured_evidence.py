"""Structured evidence ranking, name selection, and enrichment planning.

Does not fetch URLs. JSON-LD parsing lives in ``content_extraction`` so HTML
scripts can be read before they are stripped as noise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from content_extraction import BusinessSignals, PageEvidence, StructuredFact

UNKNOWN = "UNKNOWN"

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
    "cart",
    "checkout",
    "bag",
    "your bag",
    "wishlist",
    "payment",
    "thank you",
    "new arrivals",
    "best sellers",
    "sale",
    "collections",
    "collection",
    "dresses",
    "tops",
    "skirts",
    "women",
    "women's",
    "ladies",
    "store locator",
    "our story",
}

CATEGORY_EXACT_NAMES = {
    "new arrivals",
    "best sellers",
    "bestseller",
    "bestsellers",
    "sale",
    "shop sale",
    "dresses",
    "tops",
    "skirts",
    "collections",
    "collection",
    "women",
    "women's",
    "womens",
    "ladies",
    "shop women",
    "shop men's",
    "shop mens",
}

NAV_LABEL_NAMES = {
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
    "menu",
    "more",
    "read more",
    "learn more",
    "view",
    "website",
    "map",
    "maps",
    "cart",
    "account",
    "checkout",
}

CHROME_NAME_RE = re.compile(
    r"\b(cart|checkout|order summary|shopping cart|item added|added to (?:your )?cart|"
    r"my account|please login|wishlist|order confirmation|login|register|"
    r"your bag|thank you for (?:your )?order|payment successful)\b",
    re.IGNORECASE,
)
ROUNDUP_TITLE_RE = re.compile(
    r"\b(?:\d+\s+)?(?:best|top|coolest|cool|stylish)\b.{0,50}\b"
    r"(?:boutique|boutiques|stores?|shops?)\b|"
    r"\bboutiques?\s+to\s+visit\b|"
    r"\bshopping\s+(?:in|guide)\b|"
    r"\bdesigners?\s+in\b.{0,40}\bboutiques?\b|"
    r"\bwhere\s+to\s+shop\b",
    re.IGNORECASE,
)
ARTICLE_NAME_RE = re.compile(
    r"^\s*\d+\s+(best|top|coolest)\b|"
    r"\b(how to|guide to|things to|places to visit|boutiques? in)\b",
    re.IGNORECASE,
)
BOUTIQUE_TERM_RE = re.compile(
    r"\b(fashion\s+boutique|clothing\s+boutique|designer\s+boutique|"
    r"women'?s\s+boutique|ladies\s+boutique|boutique)\b",
    re.IGNORECASE,
)
STORE_TERM_RE = re.compile(
    r"\b(fashion\s+store|clothing\s+store|designer\s+store|apparel)\b",
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
WOMEN_INDICATOR_RE = re.compile(
    r"\b(women|woman|ladies|womenswear|her|she|female)\b",
    re.IGNORECASE,
)
PHYSICAL_YES_RE = re.compile(
    r"\b(visit\s+us|showroom|store\s+locator|our\s+stores?|physical\s+store|"
    r"boutique\s+address|walk[\s\-]?in|visit\s+the\s+store|flagship\s+store|"
    r"find\s+us|shop\s+in\s+[A-Z][a-z]+|book\s+an\s+appointment|"
    r"store\s+hours|opening\s+hours)\b",
    re.IGNORECASE,
)
PHYSICAL_NO_RE = re.compile(
    r"\b(online[\s\-]?only|online[\s\-]?exclusive|no\s+physical\s+store)\b",
    re.IGNORECASE,
)
PINCODE_RE = re.compile(r"\b[1-9]\d{5}\b")

NAME_SOURCE_RANK = {
    "jsonld_organization": 100,
    "jsonld_localbusiness": 95,
    "jsonld_website": 90,
    "og_site_name": 80,
    "meta_brand": 75,
    "homepage_h1": 60,
    "about_page_heading": 58,
    "about_page_title": 55,
    "homepage_title": 50,
    "contact_page_heading": 40,
    "store_page_heading": 40,
    "contact_page_title": 35,
    "store_page_title": 35,
    "search_title": 20,
    "heading": 45,
    "page_title": 48,
    "none": 0,
}

SOURCE_PRECEDENCE = {
    "jsonld_localbusiness": 100,
    "jsonld_organization": 95,
    "jsonld_website": 90,
    "jsonld_postaladdress": 88,
    "jsonld_contactpoint": 86,
    "og_site_name": 80,
    "meta_brand": 78,
    "contact_page": 70,
    "location_page": 70,
    "about_page": 65,
    "homepage_text": 50,
    "homepage_h1": 48,
    "homepage_title": 40,
    "search_title": 20,
    "search_snippet": 18,
    "none": 0,
}

CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

NAME_SOURCE_CONFIDENCE = {
    "jsonld_organization": "HIGH",
    "jsonld_localbusiness": "HIGH",
    "jsonld_website": "HIGH",
    "og_site_name": "HIGH",
    "meta_brand": "MEDIUM",
    "homepage_h1": "MEDIUM",
    "about_page_heading": "MEDIUM",
    "about_page_title": "MEDIUM",
    "homepage_title": "MEDIUM",
    "heading": "MEDIUM",
    "page_title": "MEDIUM",
    "contact_page_heading": "LOW",
    "store_page_heading": "LOW",
    "contact_page_title": "LOW",
    "store_page_title": "LOW",
    "search_title": "LOW",
}


class EnrichmentTier(str, Enum):
    STRONG = "STRONG"
    PARTIAL = "PARTIAL"
    WEAK = "WEAK"


@dataclass(frozen=True)
class NameCandidate:
    name: str
    source: str
    confidence: str
    rank: int
    raw: str = ""


@dataclass(frozen=True)
class EnrichmentDecision:
    tier: EnrichmentTier
    wanted_roles: tuple[str, ...]
    reasons: tuple[str, ...]


def looks_generic_name(text: str) -> bool:
    lowered = text.strip().lower().rstrip("!.")
    if lowered in GENERIC_NAMES or lowered in CATEGORY_EXACT_NAMES:
        return True
    if lowered in NAV_LABEL_NAMES:
        return True
    if CHROME_NAME_RE.search(lowered):
        return True
    if ARTICLE_NAME_RE.search(lowered):
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


def is_roundup_title(text: str) -> bool:
    if not text:
        return False
    if ROUNDUP_TITLE_RE.search(text):
        return True
    return bool(
        re.search(r"\b\d+\s+best\b", text, re.IGNORECASE)
        or re.search(r"\bboutiques?\s+in\s+[A-Za-z]", text, re.IGNORECASE)
    )


def clean_business_name(raw: str) -> str:
    text = re.sub(r"\s+", " ", raw or "").strip()
    if not text:
        return UNKNOWN
    if is_roundup_title(text) or looks_generic_name(text):
        return UNKNOWN
    parts = re.split(r"\s*[-–—|]\s*", text, maxsplit=1)
    if len(parts) == 2:
        left = parts[0].strip()
        if (
            left
            and not looks_generic_name(left)
            and not is_roundup_title(left)
            and len(left.split()) <= 6
        ):
            text = left
    text = re.sub(r"\s*[\(\[]@[^)\]]+[\)\]]", "", text).strip()
    stripped = re.sub(
        r"\s+(women'?s|ladies|fashion|clothing)\s+boutique$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    if (
        stripped
        and stripped != text
        and not looks_generic_name(stripped)
        and not is_roundup_title(stripped)
        and 1 <= len(stripped.split()) <= 6
    ):
        text = stripped
    if looks_generic_name(text) or is_roundup_title(text) or len(text) < 2:
        return UNKNOWN
    if len(text) > 80:
        return UNKNOWN
    if len(text.split()) > 8:
        return UNKNOWN
    return text


def clean_contextual_name(raw: str, source: str) -> str:
    text = raw
    if source.startswith("about_page"):
        text = re.sub(
            r"^\s*about(?:\s+us)?\s*[:\-–—]?\s+", "", text, flags=re.IGNORECASE
        )
        if ":" in text:
            left = text.split(":", 1)[0].strip()
            if 1 <= len(left.split()) <= 6 and clean_business_name(left) != UNKNOWN:
                text = left
    elif source.startswith("contact_page"):
        text = re.sub(
            r"^\s*contact(?:\s+us)?\s*[:\-–—]?\s+", "", text, flags=re.IGNORECASE
        )
    return clean_business_name(text)


def name_confidence_for(source: str) -> str:
    return NAME_SOURCE_CONFIDENCE.get(source, "LOW")


def collect_name_candidates(
    *,
    page: PageEvidence,
    search_title: str = "",
    page_role: str = "homepage",
) -> list[NameCandidate]:
    """Collect ranked name candidates from structured data and page chrome."""
    found: list[NameCandidate] = []

    def add(raw: str | None, source: str) -> None:
        if not raw:
            return
        cleaned = (
            clean_contextual_name(raw, source)
            if page_role in {"about", "contact", "location"}
            else clean_business_name(raw)
        )
        if cleaned == UNKNOWN:
            return
        found.append(
            NameCandidate(
                name=cleaned,
                source=source,
                confidence=name_confidence_for(source),
                rank=NAME_SOURCE_RANK.get(source, 0),
                raw=raw,
            )
        )

    for fact in page.structured_facts:
        if fact.field != "name":
            continue
        add(fact.value, fact.source)

    add(page.og_site_name, "og_site_name")
    add(page.meta_brand, "meta_brand")

    headings = list(page.headings or [])
    title = page.title or ""
    if page_role == "homepage":
        add(headings[0] if headings else None, "homepage_h1")
        add(title, "homepage_title")
    elif page_role == "about":
        add(headings[0] if headings else None, "about_page_heading")
        add(title, "about_page_title")
    elif page_role == "contact":
        add(headings[0] if headings else None, "contact_page_heading")
        add(title, "contact_page_title")
    elif page_role == "location":
        add(headings[0] if headings else None, "store_page_heading")
        add(title, "store_page_title")
    else:
        add(headings[0] if headings else None, "heading")
        add(title, "page_title")

    if search_title and search_title.strip() != (title or "").strip():
        add(search_title, "search_title")
    elif search_title and not title:
        add(search_title, "search_title")
    return found


def choose_business_name(
    candidates: list[NameCandidate],
) -> tuple[str, str, str]:
    """Return ``(name, source, confidence)``. Does not trust a single source blindly."""
    viable = [item for item in candidates if item.name and item.name != UNKNOWN]
    if not viable:
        return UNKNOWN, "none", "LOW"
    best = max(
        viable,
        key=lambda item: (
            item.rank,
            CONFIDENCE_RANK.get(item.confidence, 0),
            1 if item.source.startswith("jsonld_") else 0,
        ),
    )
    return best.name, best.source, best.confidence


def fact_strength(fact: StructuredFact) -> tuple[int, int]:
    return (
        SOURCE_PRECEDENCE.get(fact.source, 0),
        CONFIDENCE_RANK.get(fact.confidence, 0),
    )


def pick_best_fact(facts: list[StructuredFact]) -> StructuredFact | None:
    if not facts:
        return None
    return max(facts, key=fact_strength)


def stronger_fact(existing: StructuredFact | None, incoming: StructuredFact) -> StructuredFact:
    if existing is None:
        return incoming
    if fact_strength(incoming) > fact_strength(existing):
        return incoming
    return existing


def page_haystack(page: PageEvidence, extra: str = "") -> str:
    parts = [
        page.title,
        page.og_site_name,
        page.meta_brand,
        page.meta_description,
        " ".join(page.headings),
        page.text[:24000],
        extra,
    ]
    return " ".join(part for part in parts if part)


def _has_instagram(signals: BusinessSignals) -> bool:
    return any("instagram.com" in (url or "").lower() for url in signals.social_urls)


def _homepage_name_ok(page: PageEvidence) -> bool:
    name, source, confidence = choose_business_name(
        collect_name_candidates(page=page, page_role="homepage")
    )
    if name == UNKNOWN:
        return False
    return confidence in {"HIGH", "MEDIUM"} and source != "search_title"


def _has_business_type_evidence(page: PageEvidence, signals: BusinessSignals) -> bool:
    haystack = page_haystack(page)
    if BOUTIQUE_TERM_RE.search(haystack) or STORE_TERM_RE.search(haystack):
        return True
    types = {fact.schema_type or "" for fact in page.structured_facts}
    lowered = {item.lower() for item in types}
    return bool(
        lowered
        & {
            "localbusiness",
            "store",
            "clothingstore",
            "fashionstore",
            "shoestore",
            "jewelrystore",
        }
    ) or any(fact.source == "jsonld_localbusiness" for fact in page.structured_facts)


def _has_women_evidence(page: PageEvidence) -> bool:
    haystack = page_haystack(page)
    if WOMEN_HIGH_RE.search(haystack):
        return True
    return len(WOMEN_MEDIUM_RE.findall(haystack)) >= 2


def _has_physical_evidence(page: PageEvidence, signals: BusinessSignals) -> bool:
    haystack = page_haystack(page)
    if PHYSICAL_NO_RE.search(haystack):
        return True
    if any(
        (fact.schema_type or "").lower()
        in {"localbusiness", "store", "clothingstore", "fashionstore"}
        for fact in page.structured_facts
    ):
        return True
    if any(fact.field == "address" for fact in page.structured_facts):
        return True
    has_address = bool(signals.address_candidates)
    has_pin = any(PINCODE_RE.search(item) for item in signals.address_candidates)
    has_phrase = bool(PHYSICAL_YES_RE.search(haystack))
    return bool((has_address and has_pin) or (has_phrase and has_address))


def _has_city_evidence(signals: BusinessSignals, page: PageEvidence) -> bool:
    localities = [
        fact.value
        for fact in page.structured_facts
        if fact.field in {"city", "addressLocality"}
    ]
    if len(set(item.lower() for item in localities)) == 1:
        return True
    cities = list(signals.city_mentions)
    address_blob = " ".join(signals.address_candidates)
    from content_extraction import CITY_NAMES

    in_address = [
        city
        for city in CITY_NAMES
        if re.search(rf"\b{re.escape(city)}\b", address_blob, re.IGNORECASE)
    ]
    unique = list(dict.fromkeys(in_address or cities))
    return len(unique) == 1


def assess_enrichment_need(
    page: PageEvidence,
    signals: BusinessSignals,
    *,
    search_query: str = "",
) -> EnrichmentDecision:
    """Decide whether homepage evidence is strong enough to skip extra fetches."""
    del search_query  # reserved for city-family checks in identification
    name_ok = _homepage_name_ok(page)
    type_ok = _has_business_type_evidence(page, signals)
    women_ok = _has_women_evidence(page)
    physical_ok = _has_physical_evidence(page, signals)
    city_ok = _has_city_evidence(signals, page)
    contact_ok = bool(
        signals.emails or signals.phones or _has_instagram(signals)
    )

    wanted: list[str] = []
    reasons: list[str] = []
    if not contact_ok:
        wanted.append("contact")
        reasons.append("missing_contact")
    if not physical_ok or not city_ok:
        wanted.append("location")
        reasons.append("missing_location" if not physical_ok else "missing_city")
    if not name_ok or not type_ok:
        wanted.append("about")
        reasons.append("missing_identity" if not name_ok else "missing_type")
    if not women_ok or not type_ok:
        if "about" not in wanted:
            wanted.append("about")
        wanted.append("fashion")
        if not women_ok:
            reasons.append("missing_women")

    verified = (
        name_ok
        and type_ok
        and women_ok
        and physical_ok
        and city_ok
        and contact_ok
    )
    if verified:
        return EnrichmentDecision(EnrichmentTier.STRONG, (), ("complete",))

    missing = sum(
        not flag
        for flag in (name_ok, type_ok, women_ok, physical_ok, city_ok, contact_ok)
    )
    if missing >= 3:
        return EnrichmentDecision(
            EnrichmentTier.WEAK,
            ("contact", "location", "about", "business", "fashion"),
            tuple(reasons or ("weak_homepage",)),
        )
    return EnrichmentDecision(
        EnrichmentTier.PARTIAL,
        tuple(dict.fromkeys(wanted)),
        tuple(reasons),
    )
