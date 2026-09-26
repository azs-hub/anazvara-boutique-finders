"""Entity-quality gate used before stockist-lead evaluation.

Does not change Qwen or potential_stockist. Does not hard-code businesses.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from classification import ResultType, classify_url
from url_normalization import extract_domain, extract_host, normalize_url

YES_NO_UNKNOWN = {"YES", "NO", "UNKNOWN"}
DEFAULT_LOCATION = "Goa"
LISTING_SOURCES = {ResultType.DIRECTORY.value, ResultType.ARTICLE.value, ResultType.VIDEO.value}
MENTION_SIGNALS = {
    "article_business_link",
    "article_heading",
    "external_business_link",
}
MEDIA_SIGNALS = {"roundup_page", "publisher_not_boutique", "media_page"}
SOCIAL_POST_PATH_RE = re.compile(
    r"/(reel|reels|p|tv|stories|share|posts|photos|watch)/",
    re.IGNORECASE,
)
LISTICLE_PATH_RE = re.compile(
    r"/(?:blogs?|articles?|stories|story|travel-guides?)/|"
    r"shopping-in-|designers?-in-|boutiques?-to-visit|"
    r"places-to-(?:shop|visit)|/my-goa/",
    re.IGNORECASE,
)
LISTICLE_TITLE_RE = re.compile(
    r"\b(?:\d+\s+)?(?:best|top|coolest|cool|stylish)\b.{0,50}\b"
    r"(?:boutiques?|stores?|shops?)\b|"
    r"\bboutiques?\s+to\s+visit\b|"
    r"\bshopping\s+(?:in|guide|places)\b|"
    r"\bdesigners?\s+in\b.{0,40}\bboutiques?\b|"
    r"\bwhere\s+to\s+shop\b|"
    r"\bplaces\s+to\s+shop\b|"
    r"\bfashion\s+haven\b|"
    r"\btravel\s+guide\b|"
    r"\bshopping\s+advice\b",
    re.IGNORECASE,
)
MEDIA_HOST_RE = re.compile(
    r"(times|tribune|news|magazine|digest|vogue|elle|grazia|nodmag)\.",
    re.IGNORECASE,
)
HOTEL_RE = re.compile(
    r"\b(hotel|resort|spa\s+resort|heritage\s+village|resort[\s\-&]+spa|"
    r"hotel[\s\-]?owned|in[\s\-]?house\s+boutique|resort\s+boutique)\b",
    re.IGNORECASE,
)
HOTEL_PATH_RE = re.compile(
    r"/(?:resort|hotel|heritage-village|boutique-store)/",
    re.IGNORECASE,
)
GOA_RE = re.compile(
    r"\b(goa|panaji|panjim|anjuna|assagao|vagator|morjim|mapusa|margao|"
    r"candolim|calangute|parra|aldona|arossim|cansaulim|bardez|"
    r"north\s+goa|south\s+goa)\b",
    re.IGNORECASE,
)
FOREIGN_PLACE_RE = re.compile(
    r"\b(ireland|waterford|dublin|united\s+kingdom|\buk\b|london|"
    r"united\s+states|\busa\b|new\s+york|california|paris|france|"
    r"dubai|singapore|sydney|melbourne|toronto|canada|germany|"
    r"italy|spain|amsterdam)\b",
    re.IGNORECASE,
)
FOREIGN_TLDS = {
    ".ie",
    ".uk",
    ".us",
    ".au",
    ".nz",
    ".de",
    ".fr",
    ".it",
    ".es",
    ".ae",
    ".sg",
    ".ca",
}
INDEPENDENT_RETAIL_RE = re.compile(
    r"\b(independent\s+(?:retail|boutique|store)|multi[\s\-]?brand|"
    r"multi[\s\-]?designer|concept\s+store|curated\s+store)\b",
    re.IGNORECASE,
)


def _norm_url(url: str | None) -> str:
    if not url:
        return ""
    return (normalize_url(url) or url).lower()


def is_social_post_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(_norm_url(url) if "://" in (url or "") else f"https://{url}")
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if "instagram.com" not in host and "facebook.com" not in host and "fb.com" not in host:
        return False
    return bool(SOCIAL_POST_PATH_RE.search(path))


def _is_instagram_profile(url: str | None) -> bool:
    if not url or is_social_post_url(url):
        return False
    host = (extract_host(_norm_url(url) or url) or "").lower()
    return "instagram.com" in host


def _is_facebook_page(url: str | None) -> bool:
    if not url or is_social_post_url(url):
        return False
    host = (extract_host(_norm_url(url) or url) or "").lower()
    return "facebook.com" in host or "fb.com" in host


def looks_like_media_or_listicle(
    *,
    url: str | None = None,
    title: str | None = None,
    text: str = "",
    source_type: str | None = None,
    signals: list[str] | None = None,
) -> bool:
    signals = signals or []
    if source_type in LISTING_SOURCES:
        return source_type != ResultType.VIDEO.value
    if any(item in MEDIA_SIGNALS for item in signals):
        return True
    if url and classify_url(url) is ResultType.ARTICLE:
        return True
    if url and LISTICLE_PATH_RE.search(url):
        return True
    domain = extract_domain(_norm_url(url) or url or "") or ""
    if MEDIA_HOST_RE.search(domain):
        return True
    hay = " ".join(part for part in (title, text[:400]) if part)
    return bool(LISTICLE_TITLE_RE.search(hay))


def looks_like_hotel_resort(*, url: str | None, title: str | None, text: str) -> bool:
    hay = " ".join(part for part in (url, title, text[:800]) if part)
    if HOTEL_RE.search(hay):
        return True
    return bool(HOTEL_PATH_RE.search(url or "") and HOTEL_RE.search(url or ""))


def _profile_location_text(
    *,
    name: str | None,
    city: str | None,
    address: str | None,
    instagram: str | None,
    facebook: str | None,
    page_text: str,
) -> str:
    handles = []
    for url in (instagram, facebook):
        if not url:
            continue
        path = urlparse(_norm_url(url) if "://" in url else f"https://{url}").path.strip("/")
        handles.append(path.split("/")[0] if path else "")
    return " ".join(
        part
        for part in (name, city, address, " ".join(handles), page_text[:1500])
        if part
    )


def _foreign_tld(url: str | None) -> bool:
    host = (extract_host(_norm_url(url) or url or "") or "").lower()
    return any(host.endswith(tld) for tld in FOREIGN_TLDS)


def assess_geographic_relevance(
    *,
    name: str | None,
    city: str | None,
    address: str | None,
    website: str | None,
    instagram: str | None,
    facebook: str | None,
    page_text: str = "",
    expected_location: str = DEFAULT_LOCATION,
) -> tuple[str, list[str]]:
    """Location must come from the entity, not the search query."""
    del expected_location
    profile = _profile_location_text(
        name=name,
        city=city,
        address=address,
        instagram=instagram,
        facebook=facebook,
        page_text=page_text,
    )
    goa_hits = [match.group(0) for match in GOA_RE.finditer(profile)]
    foreign_hits = [match.group(0) for match in FOREIGN_PLACE_RE.finditer(profile)]
    foreign_site = _foreign_tld(website)
    evidence: list[str] = []
    if goa_hits:
        evidence.append("location_in_official_profile")
    if address and GOA_RE.search(address):
        evidence.append("goa_address")
    if city and GOA_RE.search(city):
        evidence.append("goa_city")
    if foreign_hits:
        evidence.append("foreign_place:" + ",".join(sorted(set(h.lower() for h in foreign_hits))))
    if foreign_site:
        evidence.append("foreign_website_tld")
    goa_home = bool(
        (address and GOA_RE.search(address)) or (city and GOA_RE.search(city))
    )
    if foreign_site and not goa_home:
        return "NO", evidence or ["foreign_website_tld"]
    if (foreign_hits or foreign_site) and not goa_hits:
        return "NO", evidence or ["wrong_country"]
    if goa_hits:
        return "YES", evidence
    return "UNKNOWN", evidence


def _entity_relationship(
    *,
    url: str | None,
    source_type: str | None,
    signals: list[str],
    title: str | None,
    text: str,
    named: bool,
) -> str:
    if is_social_post_url(url):
        return "SOCIAL_POST"
    if source_type == ResultType.DIRECTORY.value or "directory" in signals:
        return "DIRECTORY"
    if any(item in signals for item in MENTION_SIGNALS):
        return "MENTIONED_BUSINESS"
    if looks_like_media_or_listicle(
        url=url, title=title, text=text, source_type=source_type, signals=signals
    ):
        if source_type == ResultType.ARTICLE.value or MEDIA_HOST_RE.search(
            extract_domain(_norm_url(url) or url or "") or ""
        ):
            return "MEDIA"
        return "ARTICLE"
    if source_type == ResultType.SOCIAL.value and named and not is_social_post_url(url):
        return "SELF"
    if named and source_type == ResultType.WEBSITE.value:
        return "SELF"
    if named:
        return "SELF"
    return "UNKNOWN"


def _business_context(
    relationship: str,
    *,
    hotel: bool,
    business_type: str | None,
    text: str,
) -> str:
    if relationship in {"ARTICLE", "MEDIA"}:
        return "MEDIA"
    if relationship == "DIRECTORY":
        return "DIRECTORY"
    if hotel:
        return "HOTEL_RESORT_BOUTIQUE"
    typed = (business_type or "").upper()
    if typed == "MARKETPLACE":
        return "MARKETPLACE"
    if typed in {"DESIGNER", "BRAND"}:
        return typed
    if typed in {"BOUTIQUE", "MULTI_DESIGNER", "MULTI_BRAND", "CONCEPT_STORE"}:
        return "INDEPENDENT_RETAIL"
    if INDEPENDENT_RETAIL_RE.search(text):
        return "INDEPENDENT_RETAIL"
    return "UNKNOWN"


def _official_identity_ok(
    *,
    website: str | None,
    instagram: str | None,
    facebook: str | None,
    page_text: str,
    relationship: str,
) -> bool:
    if relationship != "SELF":
        return False
    if website and not looks_like_media_or_listicle(url=website, title=None, text=""):
        return True
    if _is_instagram_profile(instagram):
        return True
    if _is_facebook_page(facebook) and INDEPENDENT_RETAIL_RE.search(page_text):
        return True
    return False


def assess_entity_quality(
    *,
    url: str | None,
    title: str | None = None,
    text: str = "",
    source_type: str | None = None,
    signals: list[str] | None = None,
    name: str | None = None,
    city: str | None = None,
    address: str | None = None,
    website: str | None = None,
    instagram: str | None = None,
    facebook: str | None = None,
    business_type: str | None = None,
    expected_location: str = DEFAULT_LOCATION,
) -> dict[str, Any]:
    signals = list(signals or [])
    named = bool(name) and name != "UNKNOWN"
    relationship = _entity_relationship(
        url=url,
        source_type=source_type,
        signals=signals,
        title=title,
        text=text,
        named=named,
    )
    hotel = looks_like_hotel_resort(url=url, title=title, text=text)
    geo, geo_evidence = assess_geographic_relevance(
        name=name,
        city=city,
        address=address,
        website=website,
        instagram=instagram,
        facebook=facebook,
        page_text=text,
        expected_location=expected_location,
    )
    context = _business_context(
        relationship, hotel=hotel, business_type=business_type, text=text
    )
    is_business = "NO"
    if relationship == "SELF" and named:
        is_business = "YES"
    elif relationship == "MENTIONED_BUSINESS" and named:
        is_business = "YES"
    elif relationship in {"ARTICLE", "MEDIA", "DIRECTORY", "SOCIAL_POST"}:
        is_business = "NO"
    identity_ok = _official_identity_ok(
        website=website,
        instagram=instagram,
        facebook=facebook,
        page_text=text,
        relationship=relationship,
    )
    reasons: list[str] = [f"relationship:{relationship}"]
    if hotel:
        reasons.append("hotel_resort_context")
    if geo == "NO":
        reasons.append("geographic_mismatch")
    if not named:
        reasons.append("identity_missing")
    if relationship == "SELF" and not identity_ok:
        reasons.append("official_identity_weak")

    quality = "LOW"
    if is_business == "NO" or relationship in {"ARTICLE", "MEDIA", "DIRECTORY", "SOCIAL_POST"}:
        quality = "REJECTED"
    elif geo == "NO":
        quality = "REJECTED"
    elif relationship == "SELF" and identity_ok and geo == "YES":
        quality = "HIGH"
    elif relationship == "SELF" and identity_ok:
        quality = "MEDIUM"
    elif relationship == "SELF":
        quality = "WEAK"

    excluded = True
    exclude_reason = None
    if (
        relationship == "SELF"
        and is_business == "YES"
        and geo == "YES"
        and identity_ok
        and context != "HOTEL_RESORT_BOUTIQUE"
    ):
        excluded = False
    elif relationship in {"ARTICLE", "MEDIA", "DIRECTORY", "SOCIAL_POST"}:
        exclude_reason = f"not_self_business:{relationship}"
    elif geo == "NO":
        exclude_reason = "wrong_geography"
    elif context == "HOTEL_RESORT_BOUTIQUE":
        exclude_reason = "hotel_resort_boutique"
    elif not identity_ok:
        exclude_reason = "official_identity_weak"
    else:
        exclude_reason = "entity_quality_insufficient"

    return {
        "entity_is_business": is_business,
        "entity_relationship": relationship,
        "geographic_relevance": geo,
        "geographic_evidence": geo_evidence,
        "business_context": context,
        "entity_quality": quality,
        "excluded_from_lead_eval": excluded,
        "exclude_reason": exclude_reason,
        "entity_quality_reasons": reasons,
    }
