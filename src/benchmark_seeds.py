"""Benchmark-only seed businesses for stockist discovery tests.

Seeds are not production rules and do not set potential_stockist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from urllib.parse import urlparse

from business_candidates import (
    UNKNOWN,
    BusinessCandidate,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
    identify_business_candidates,
)
from candidates import Candidate, candidate_from_search_result, candidates_from_search_results
from classification import (
    ARTICLE_DOMAINS,
    DIRECTORY_DOMAINS,
    SOCIAL_DOMAINS,
    ResultType,
    classify_url,
    is_google_maps_url,
    _host_matches,
)
from content_extraction import (
    PageEvidence,
    empty_evidence,
    empty_signals,
    extract_business_signals,
    extract_page_evidence,
)
from enrichment import EnrichedEvidence, enrich_candidate, is_same_site
from local_llm import LocalLLMClient, maybe_classify_with_local_llm
from search_provider import SearchResult
from searxng_provider import SearXNGSearchProvider
from url_normalization import extract_domain, normalize_url

SEED_SOURCE = "SEED_REFERENCE"
ORIGIN_SEED = "SEED_REFERENCE"
ORIGIN_DIRECT = "DIRECT_WEBSITE"
IDENTITY_WEBSITE = "WEBSITE"
IDENTITY_INSTAGRAM = "INSTAGRAM"
IDENTITY_FACEBOOK = "FACEBOOK"
IDENTITY_GOOGLE_MAPS = "GOOGLE_MAPS"
IDENTITY_MULTIPLE = "MULTIPLE"
VERIFIED_WEBSITE = "OFFICIAL_WEBSITE"
VERIFIED_INSTAGRAM = "OFFICIAL_INSTAGRAM"
VERIFIED_FACEBOOK = "OFFICIAL_FACEBOOK"
VERIFIED_GOOGLE = "GOOGLE_BUSINESS"
RELATION_UNRESOLVED = "UNRESOLVED"
RELATION_OFFICIAL_SITE = "OFFICIAL_WEBSITE"
RELATION_OFFICIAL_SOCIAL = "OFFICIAL_SOCIAL"
RELATION_GOOGLE = "GOOGLE_BUSINESS"
SEED_STOPWORDS = {
    "goa",
    "boutique",
    "shop",
    "the",
    "and",
    "store",
    "stores",
    "collective",
    "keeper",
    "fashion",
}
SEED_STOCKIST_PAGE_RE = re.compile(
    r"\b(about|our[\s\-]?story|who[\s\-]?we[\s\-]?are|designers?|brands?|"
    r"stockists?|stores?|contact|wholesale|sell[\s\-]?with[\s\-]?us|"
    r"collaborat|become[\s\-]?a[\s\-]?brand|vendor|brand[\s\-]?enquir|"
    r"faq|makers?|artisans?|curated)\b",
    re.IGNORECASE,
)
SEED_EXCERPT_HINT_RE = re.compile(
    r"\b(multi[\s\-]?brand|multi[\s\-]?designer|independent (?:designers?|brands?|"
    r"artisans?|makers?)|curated|concept store|stockists?|brand book|catalogue|"
    r"50\+|50 independent|sell (?:your )?products?|wholesale)\b",
    re.IGNORECASE,
)
LISTING_TITLE_RE = re.compile(
    r"\b(airbnb|shopee|superstock|shutterstock|justdial|tripadvisor|"
    r"stock photos?|things to do|best shops?|top \d+|directory|listing|"
    r"for sale|book now|places to stay|holiday home|our favorite|"
    r"favorite designer|hotels?)\b",
    re.IGNORECASE,
)
LISTING_PATH_RE = re.compile(
    r"/(listings?|hotels?|hotel|blog|blogs|article|articles|news|"
    r"stories|story|reviews?|places)/",
    re.IGNORECASE,
)
LOCATION_RE = re.compile(
    r"\b(goa|parra|assagao|anjuna|panjim|panaji|mapusa|calangute|"
    r"vagator|morjim|candolim|saligao)\b",
    re.IGNORECASE,
)
SOCIAL_SKIP_SEGMENTS = {
    "p",
    "reel",
    "reels",
    "stories",
    "explore",
    "watch",
    "share",
    "sharer",
    "posts",
    "photos",
    "videos",
    "story",
    "pages",
    "people",
    "groups",
    "login",
    "signup",
}
MAX_SEED_EXTRA_PAGES = 3
MAX_SEED_EXCERPT = 1_500


@dataclass(frozen=True)
class SeedSpec:
    """One known market candidate. Classification is never taken from this."""

    seed_name: str
    city: str = "Goa"
    url: str | None = None
    instagram: str | None = None
    facebook: str | None = None
    aliases: tuple[str, ...] = ()
    reference_expected_stockist: str | None = None


GOA_STOCKIST_SEEDS: tuple[SeedSpec, ...] = (
    SeedSpec(
        "Yellow House Parra",
        aliases=("Yellow House",),
        reference_expected_stockist="YES",
    ),
    SeedSpec(
        "Syne Goa",
        facebook="https://www.facebook.com/synegoa/",
        aliases=("Syne",),
    ),
    SeedSpec(
        "Rangeen Goa",
        instagram="https://www.instagram.com/rangeengoa/",
        aliases=("Rangeen",),
    ),
    SeedSpec(
        "The Good Life Goa",
        facebook="https://www.facebook.com/TheGoodLifeGoaTGL/",
        aliases=("The Good Life", "Good Life Goa"),
        reference_expected_stockist="YES",
    ),
    SeedSpec(
        "Rangeela Goa",
        url="https://rangeelagoa.com",
        aliases=("Rangeela",),
        reference_expected_stockist="YES",
    ),
    SeedSpec(
        "Sosa Goa Boutique",
        instagram="https://www.instagram.com/sosas.goaboutique/",
        facebook="https://www.facebook.com/Sosasgoa/",
        aliases=("Sosa's", "Sosas", "Sosa Goa"),
    ),
    SeedSpec(
        "Sasha's Shop",
        instagram="https://www.instagram.com/sachatheshopkeeper/",
        aliases=(
            "Sasha the Shop Keeper",
            "Sasha the Shopkeeper",
            "Sacha the Shop Keeper",
            "Sasha Shop",
        ),
    ),
    SeedSpec(
        "Paper Boat Collective",
        instagram="https://www.instagram.com/paperboatcollective/",
        aliases=("Paper Boat",),
        reference_expected_stockist="YES",
    ),
)


@dataclass
class SeedResolution:
    seed: SeedSpec
    discovered: bool = False
    identity_found: bool = False
    identity_verified: bool = False
    identity_type: str | None = None
    website: str | None = None
    official_website_verified: bool = False
    instagram_url: str | None = None
    instagram_verified: bool = False
    facebook_url: str | None = None
    facebook_verified: bool = False
    google_maps_url: str | None = None
    google_business_verified: bool = False
    identity_sources: list[str] = field(default_factory=list)
    entity_verified_from: str | None = None
    entity_relationship: str = RELATION_UNRESOLVED
    candidate: Candidate | None = None
    resolution_path: str = "unresolved"
    notes: list[str] = field(default_factory=list)
    organic_hit: bool = False
    identity_signals: list[str] = field(default_factory=list)

    @property
    def official_site_found(self) -> bool:
        return bool(self.website)

    @property
    def official_site_verified(self) -> bool:
        return self.official_website_verified

    @property
    def official_url(self) -> str | None:
        return self.website


def seed_names(seed: SeedSpec) -> list[str]:
    return [seed.seed_name, *seed.aliases]


def seed_tokens(name: str) -> list[str]:
    parts = re.findall(r"[a-z0-9]+", name.lower())
    return [part for part in parts if part not in SEED_STOPWORDS and len(part) > 2]


def _compact(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", text.lower()))


def _haystack(*parts: str | None) -> str:
    return " ".join(part for part in parts if part)


def page_mentions_seed(seed: SeedSpec, page: PageEvidence | None, extra: str = "") -> bool:
    if page is None and not extra:
        return False
    hay = " ".join(
        part
        for part in (
            extra,
            getattr(page, "title", None),
            getattr(page, "og_site_name", None),
            getattr(page, "meta_description", None),
            " ".join(getattr(page, "headings", None) or []),
            (getattr(page, "text", None) or "")[:800],
        )
        if part
    ).lower()
    return _name_in_text(seed, hay)


def _name_in_text(seed: SeedSpec, hay: str) -> bool:
    lowered = hay.lower()
    for name in seed_names(seed):
        if name.lower() in lowered:
            return True
    tokens = seed_tokens(seed.seed_name)
    if not tokens:
        return False
    return all(token in lowered for token in tokens)


def _identity_surface(page: PageEvidence | None, extra: str = "", url: str | None = None) -> str:
    return _haystack(
        extra,
        getattr(page, "title", None),
        getattr(page, "og_site_name", None),
        extract_domain(url or "") if url else None,
    )


def is_rejected_website_host(url: str | None) -> bool:
    if not url:
        return True
    domain = extract_domain(normalize_url(url) or url) or ""
    return _host_matches(domain, SOCIAL_DOMAINS | DIRECTORY_DOMAINS | ARTICLE_DOMAINS)


def is_usable_official_url(url: str | None) -> bool:
    if not url or is_google_maps_url(url):
        return False
    normalized = normalize_url(url) or url
    if classify_url(normalized) is not ResultType.WEBSITE:
        return False
    return not is_rejected_website_host(normalized)


def looks_like_listing_title(title: str | None) -> bool:
    return bool(title and LISTING_TITLE_RE.search(title))


def _city_parts(seed: SeedSpec) -> list[str]:
    return [part for part in re.findall(r"[a-z0-9]+", seed.city.lower()) if len(part) > 2]


def _strong_name_in_text(seed: SeedSpec, hay: str) -> bool:
    lowered = hay.lower()
    if seed.seed_name.lower() in lowered:
        return True
    compact_hay = _compact(hay)
    compact_name = _compact(seed.seed_name)
    if len(compact_name) >= 8 and compact_name in compact_hay:
        return True
    tokens = seed_tokens(seed.seed_name)
    city_parts = _city_parts(seed)
    if tokens and city_parts and all(part in lowered for part in [*tokens, *city_parts]):
        return True
    return False


def domain_matches_seed(seed: SeedSpec, url: str | None) -> bool:
    domain = extract_domain(normalize_url(url or "") or url or "") or ""
    if not domain:
        return False
    compact_domain = _compact(domain)
    compact_name = _compact(seed.seed_name)
    if len(compact_name) >= 8 and compact_name in compact_domain:
        return True
    tokens = seed_tokens(seed.seed_name)
    city_parts = _city_parts(seed)
    if tokens and city_parts and all(part in compact_domain for part in [*tokens, *city_parts]):
        return True
    return False


def _homepage_path(url: str | None) -> bool:
    parsed = urlparse(normalize_url(url or "") or url or "")
    path = (parsed.path or "").strip("/")
    return path == "" or path.lower() in {"index", "index.html", "home"}


def official_website_identity_match(
    seed: SeedSpec,
    page: PageEvidence | None,
    *,
    url: str | None = None,
    extra: str = "",
) -> bool:
    """True only when the page appears to *be* the business, not mention it."""
    if not is_usable_official_url(url):
        return False
    title = extra or getattr(page, "title", None)
    if looks_like_listing_title(title) or looks_like_listing_title(
        getattr(page, "title", None)
    ):
        return False
    if LISTING_PATH_RE.search((urlparse(url or "").path or "") + "/"):
        return False
    if domain_matches_seed(seed, url):
        return True
    if not _homepage_path(url):
        return False
    surface = _identity_surface(page, extra=extra, url=url)
    return _strong_name_in_text(seed, surface)


def social_handle(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(normalize_url(url) or url)
    parts = [part for part in (parsed.path or "").split("/") if part]
    if not parts:
        return None
    handle = parts[0]
    if handle.lower() in SOCIAL_SKIP_SEGMENTS:
        return None
    if handle.lower() in {"profile.php", "pages"}:
        return None
    return handle


def handle_matches_seed(seed: SeedSpec, url: str | None) -> bool:
    handle = social_handle(url)
    if not handle:
        return False
    compact_handle = _compact(handle)
    for name in seed_names(seed):
        compact_name = _compact(name)
        if compact_name and compact_name in compact_handle:
            return True
        tokens = seed_tokens(name)
        if tokens and all(token in compact_handle for token in tokens):
            return True
    return False


def strong_handle_match(seed: SeedSpec, url: str | None) -> bool:
    """Full seed name, or distinctive tokens plus city, must appear in the handle."""
    handle = social_handle(url)
    if not handle:
        return False
    compact_handle = _compact(handle)
    compact_name = _compact(seed.seed_name)
    if compact_name and compact_name in compact_handle:
        return True
    tokens = seed_tokens(seed.seed_name)
    city_parts = [part for part in re.findall(r"[a-z0-9]+", seed.city.lower()) if len(part) > 2]
    if tokens and city_parts and all(part in compact_handle for part in [*tokens, *city_parts]):
        return True
    return False


def location_mentioned(*parts: str | None) -> bool:
    return bool(LOCATION_RE.search(_haystack(*parts)))


def verify_social_identity(
    seed: SeedSpec,
    *,
    url: str | None,
    title: str = "",
    snippet: str = "",
    page: PageEvidence | None = None,
    supplied: bool = False,
) -> tuple[bool, list[str]]:
    """Accept a social profile only with name/handle plus location or a known URL."""
    if classify_url(url or "") is not ResultType.SOCIAL:
        return False, []
    if social_handle(url) is None:
        return False, ["social_path_not_profile"]
    hay = _haystack(
        title,
        snippet,
        getattr(page, "title", None),
        getattr(page, "og_site_name", None),
        getattr(page, "meta_description", None),
        (getattr(page, "text", None) or "")[:800],
        social_handle(url),
    )
    signals: list[str] = []
    name_ok = _name_in_text(seed, hay)
    handle_ok = handle_matches_seed(seed, url)
    strong_handle = strong_handle_match(seed, url)
    location_ok = location_mentioned(hay)
    if name_ok:
        signals.append("name_match")
    if handle_ok:
        signals.append("handle_match")
    if strong_handle:
        signals.append("strong_handle_match")
    if location_ok:
        signals.append("location_match")
    if supplied:
        signals.append("supplied_official_url")
    if supplied and (handle_ok or name_ok):
        return True, signals
    if strong_handle and (name_ok or location_ok):
        return True, signals
    if name_ok and location_ok and handle_ok:
        return True, signals
    return False, signals or ["social_identity_unverified"]


def verify_google_business(
    seed: SeedSpec,
    *,
    url: str | None,
    title: str = "",
    snippet: str = "",
) -> tuple[bool, list[str]]:
    if not is_google_maps_url(url):
        return False, []
    hay = _haystack(title, snippet)
    signals: list[str] = []
    if _name_in_text(seed, hay):
        signals.append("name_match")
    if location_mentioned(hay):
        signals.append("location_match")
    if "name_match" in signals and "location_match" in signals:
        return True, signals
    return False, signals or ["google_business_unverified"]


def looks_like_seed_stockist_page(url: str, anchor: str = "") -> bool:
    blob = f"{url} {anchor}"
    return bool(SEED_STOCKIST_PAGE_RE.search(blob))


def organic_mentions_seed(seed: SeedSpec, candidates: list[Candidate]) -> bool:
    for candidate in candidates:
        hay = " ".join(
            part for part in (candidate.title, candidate.url, candidate.snippet) if part
        )
        if page_mentions_seed(seed, None, extra=hay):
            return True
    return False


def _candidate_for_url(
    url: str,
    seed: SeedSpec,
    *,
    title: str | None = None,
    source: str = SEED_SOURCE,
) -> Candidate | None:
    result = SearchResult(
        title=title or seed.seed_name,
        url=url,
        snippet=seed.city,
        source=source,
    )
    candidate = candidate_from_search_result(result, f'"{seed.seed_name}" {seed.city}')
    if candidate is None:
        return None
    return replace(candidate, search_source=SEED_SOURCE, title=title or seed.seed_name)


def _fetch_evidence(fetcher, url: str) -> PageEvidence | None:
    fetch = fetcher.fetch(url)
    if not fetch.fetched or not fetch.html:
        return None
    return extract_page_evidence(
        fetch.html, source_url=url, final_url=fetch.final_url
    )


def _links_from_page(page: PageEvidence | None) -> list[str]:
    if page is None:
        return []
    urls: list[str] = []
    for link in page.links:
        target = link.normalized_url or link.url
        if target:
            urls.append(target)
    for match in re.findall(r"https?://[^\s<>\"']+", page.text or ""):
        urls.append(match)
    return urls


def _website_from_identity_page(page: PageEvidence | None) -> str | None:
    for target in _links_from_page(page):
        if is_usable_official_url(target):
            return normalize_url(target) or target
    return None


def _social_from_page(page: PageEvidence | None, host: str) -> str | None:
    for target in _links_from_page(page):
        domain = extract_domain(target) or ""
        if host in domain and social_handle(target):
            return normalize_url(target) or target
    return None


def _search_hits(
    provider: SearXNGSearchProvider, query: str, limit: int = 8
) -> list[Candidate]:
    raw = provider.search(query, page=1)
    return candidates_from_search_results(raw, query)[:limit]


def _identity_type(resolution: SeedResolution) -> str | None:
    kinds = [
        kind
        for kind, present in (
            (IDENTITY_WEBSITE, resolution.official_website_verified),
            (IDENTITY_INSTAGRAM, resolution.instagram_verified),
            (IDENTITY_FACEBOOK, resolution.facebook_verified),
            (IDENTITY_GOOGLE_MAPS, resolution.google_business_verified),
        )
        if present
    ]
    if len(kinds) > 1:
        return IDENTITY_MULTIPLE
    return kinds[0] if kinds else None


def _verified_from(resolution: SeedResolution) -> str | None:
    parts = [
        label
        for label, present in (
            (VERIFIED_WEBSITE, resolution.official_website_verified),
            (VERIFIED_INSTAGRAM, resolution.instagram_verified),
            (VERIFIED_FACEBOOK, resolution.facebook_verified),
            (VERIFIED_GOOGLE, resolution.google_business_verified),
        )
        if present
    ]
    return " + ".join(parts) if parts else None


def _relationship(resolution: SeedResolution) -> str:
    if resolution.official_website_verified:
        return RELATION_OFFICIAL_SITE
    if resolution.instagram_verified or resolution.facebook_verified:
        return RELATION_OFFICIAL_SOCIAL
    if resolution.google_business_verified:
        return RELATION_GOOGLE
    return RELATION_UNRESOLVED


def _finalize_identity(result: SeedResolution) -> SeedResolution:
    sources: list[str] = []
    if result.official_website_verified and result.website:
        sources.append(VERIFIED_WEBSITE)
    if result.instagram_verified and result.instagram_url:
        sources.append(VERIFIED_INSTAGRAM)
    if result.facebook_verified and result.facebook_url:
        sources.append(VERIFIED_FACEBOOK)
    if result.google_business_verified and result.google_maps_url:
        sources.append(VERIFIED_GOOGLE)
    result.identity_sources = sources
    result.identity_type = _identity_type(result)
    result.entity_verified_from = _verified_from(result)
    result.entity_relationship = _relationship(result)
    result.identity_verified = bool(sources)
    result.identity_found = bool(
        sources
        or result.website
        or result.instagram_url
        or result.facebook_url
        or result.google_maps_url
    )
    if result.official_website_verified and result.website:
        result.candidate = result.candidate or _candidate_for_url(result.website, result.seed)
        if result.resolution_path == "unresolved":
            result.resolution_path = "official_website"
    elif result.instagram_verified and result.instagram_url:
        result.candidate = result.candidate or _candidate_for_url(
            result.instagram_url, result.seed
        )
        if result.resolution_path == "unresolved":
            result.resolution_path = "official_instagram"
    elif result.facebook_verified and result.facebook_url:
        result.candidate = result.candidate or _candidate_for_url(
            result.facebook_url, result.seed
        )
        if result.resolution_path == "unresolved":
            result.resolution_path = "official_facebook"
    elif result.google_business_verified and result.google_maps_url:
        result.candidate = result.candidate or _candidate_for_url(
            result.google_maps_url, result.seed
        )
        if result.resolution_path == "unresolved":
            result.resolution_path = "google_business"
    if not result.identity_verified:
        result.notes.append("unresolved")
    return result


def resolve_seed(
    seed: SeedSpec,
    provider: SearXNGSearchProvider,
    fetcher,
    *,
    organic_candidates: list[Candidate] | None = None,
) -> SeedResolution:
    """Resolve a seed to an official business identity. Benchmark only."""
    result = SeedResolution(
        seed=seed,
        instagram_url=seed.instagram,
        facebook_url=seed.facebook,
        organic_hit=organic_mentions_seed(seed, organic_candidates or []),
    )

    if seed.url and is_usable_official_url(seed.url):
        page = _fetch_evidence(fetcher, seed.url)
        if page and official_website_identity_match(seed, page, url=seed.url):
            result.discovered = True
            result.website = normalize_url(seed.url) or seed.url
            result.official_website_verified = True
            result.candidate = _candidate_for_url(result.website, seed)
            result.resolution_path = "supplied_url"
            result.identity_signals.append("supplied_website")
            result.instagram_url = result.instagram_url or _social_from_page(page, "instagram.com")
            result.facebook_url = result.facebook_url or _social_from_page(page, "facebook.com")
        else:
            result.notes.append("supplied_url_failed_verification")

    queries = [
        f'"{seed.seed_name}" {seed.city}',
        f"{seed.seed_name} {seed.city} boutique",
        f'"{seed.seed_name}" {seed.city} instagram',
        f'"{seed.seed_name}" {seed.city} facebook',
    ]
    for alias in seed.aliases[:2]:
        queries.append(f'"{alias}" {seed.city}')

    website_hits: list[Candidate] = []
    instagram_hits: list[Candidate] = []
    facebook_hits: list[Candidate] = []
    maps_hits: list[Candidate] = []
    seen: set[str] = set()
    for query in queries:
        for candidate in _search_hits(provider, query):
            result.discovered = True
            key = candidate.normalized_url or candidate.url
            if key in seen:
                continue
            seen.add(key)
            url = candidate.normalized_url or candidate.url
            if is_google_maps_url(url):
                maps_hits.append(candidate)
            elif candidate.result_type is ResultType.SOCIAL and "instagram.com" in url:
                instagram_hits.append(candidate)
            elif candidate.result_type is ResultType.SOCIAL and (
                "facebook.com" in url or "fb.com" in url
            ):
                facebook_hits.append(candidate)
            elif candidate.result_type is ResultType.WEBSITE:
                website_hits.append(candidate)

    if not result.official_website_verified:
        for candidate in website_hits:
            if not is_usable_official_url(candidate.normalized_url):
                result.notes.append(f"rejected_host:{extract_domain(candidate.normalized_url)}")
                continue
            page = _fetch_evidence(fetcher, candidate.normalized_url)
            if official_website_identity_match(
                seed, page, url=candidate.normalized_url, extra=candidate.title
            ):
                result.website = candidate.normalized_url
                result.official_website_verified = True
                result.candidate = replace(candidate, search_source=SEED_SOURCE)
                result.resolution_path = "search_website"
                result.instagram_url = result.instagram_url or _social_from_page(
                    page, "instagram.com"
                )
                result.facebook_url = result.facebook_url or _social_from_page(
                    page, "facebook.com"
                )
                break
            result.notes.append("website_mention_only")

    if seed.instagram:
        instagram_hits.insert(0, _candidate_for_url(seed.instagram, seed) or Candidate(
            title=seed.seed_name,
            url=seed.instagram,
            normalized_url=normalize_url(seed.instagram) or seed.instagram,
            domain="instagram.com",
            snippet=seed.city,
            result_type=ResultType.SOCIAL,
            search_query=f'"{seed.seed_name}" {seed.city}',
            search_source=SEED_SOURCE,
        ))
    if seed.facebook:
        facebook_hits.insert(0, _candidate_for_url(seed.facebook, seed) or Candidate(
            title=seed.seed_name,
            url=seed.facebook,
            normalized_url=normalize_url(seed.facebook) or seed.facebook,
            domain="facebook.com",
            snippet=seed.city,
            result_type=ResultType.SOCIAL,
            search_query=f'"{seed.seed_name}" {seed.city}',
            search_source=SEED_SOURCE,
        ))

    for social in instagram_hits:
        url = social.normalized_url or social.url
        page = _fetch_evidence(fetcher, url)
        ok, signals = verify_social_identity(
            seed,
            url=url,
            title=social.title,
            snippet=social.snippet,
            page=page,
            supplied=bool(seed.instagram and (normalize_url(seed.instagram) or seed.instagram) == (normalize_url(url) or url)),
        )
        result.identity_signals.extend(f"instagram:{item}" for item in signals)
        if not ok:
            continue
        result.instagram_url = url
        result.instagram_verified = True
        website = _website_from_identity_page(page)
        if website and not result.official_website_verified:
            site_page = _fetch_evidence(fetcher, website)
            if official_website_identity_match(seed, site_page, url=website):
                result.website = website
                result.official_website_verified = True
                result.candidate = _candidate_for_url(website, seed)
                result.resolution_path = "instagram_website"
            else:
                result.notes.append("instagram_website_unverified")
        result.facebook_url = result.facebook_url or _social_from_page(page, "facebook.com")
        break

    for social in facebook_hits:
        url = social.normalized_url or social.url
        page = _fetch_evidence(fetcher, url)
        ok, signals = verify_social_identity(
            seed,
            url=url,
            title=social.title,
            snippet=social.snippet,
            page=page,
            supplied=bool(
                seed.facebook
                and (normalize_url(seed.facebook) or seed.facebook) == (normalize_url(url) or url)
            ),
        )
        result.identity_signals.extend(f"facebook:{item}" for item in signals)
        if not ok:
            continue
        result.facebook_url = url
        result.facebook_verified = True
        website = _website_from_identity_page(page)
        if website and not result.official_website_verified:
            site_page = _fetch_evidence(fetcher, website)
            if official_website_identity_match(seed, site_page, url=website):
                result.website = website
                result.official_website_verified = True
                result.candidate = _candidate_for_url(website, seed)
                result.resolution_path = "facebook_website"
            else:
                result.notes.append("facebook_website_unverified")
        result.instagram_url = result.instagram_url or _social_from_page(page, "instagram.com")
        break

    for maps in maps_hits:
        url = maps.normalized_url or maps.url
        ok, signals = verify_google_business(
            seed, url=url, title=maps.title, snippet=maps.snippet
        )
        result.identity_signals.extend(f"google:{item}" for item in signals)
        if not ok:
            continue
        result.google_maps_url = url
        result.google_business_verified = True
        break

    return _finalize_identity(result)


def select_seed_stockist_urls(enriched: EnrichedEvidence) -> list[str]:
    if not enriched.pages:
        return []
    seen = {normalize_url(url) or url for url in enriched.attempted_urls}
    ranked: list[tuple[int, str]] = []
    root = enriched.pages[0].final_url or enriched.root_url
    for link in enriched.combined_links():
        url = link.normalized_url or normalize_url(link.url) or link.url
        if not url or (normalize_url(url) or url) in seen:
            continue
        if not is_same_site(root, url):
            continue
        if not looks_like_seed_stockist_page(url, link.anchor_text):
            continue
        score = 20
        if SEED_EXCERPT_HINT_RE.search(f"{url} {link.anchor_text}"):
            score += 10
        ranked.append((score, url))
        seen.add(normalize_url(url) or url)
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [url for _score, url in ranked[:MAX_SEED_EXTRA_PAGES]]


def add_seed_stockist_pages(fetcher, enriched: EnrichedEvidence) -> EnrichedEvidence:
    """Fetch About/Designers/Brands/FAQ pages even when identity enrichment skipped."""
    for url in select_seed_stockist_urls(enriched):
        if len(enriched.pages) >= 1 + MAX_SEED_EXTRA_PAGES:
            break
        enriched.attempted_urls.append(url)
        page = _fetch_evidence(fetcher, url)
        if page is None:
            enriched.failed_urls.append(url)
            continue
        if page.final_url and not is_same_site(enriched.root_url, page.final_url):
            enriched.failed_urls.append(url)
            continue
        enriched.pages.append(page)
        enriched.successful_urls.append(page.final_url or url)
        enriched.selected_urls.append(url)
        enriched.selected_url_sources.append({"url": url, "source": "seed_stockist"})
    return enriched


def seed_page_evidence_for_llm(enriched: EnrichedEvidence) -> PageEvidence:
    """Prefer stockist-relevant excerpts in the first 1500 characters."""
    combined = enriched.as_page_evidence()
    text = combined.text or ""
    windows: list[str] = []
    for match in SEED_EXCERPT_HINT_RE.finditer(text):
        start = max(0, match.start() - 160)
        end = min(len(text), match.end() + 220)
        windows.append(text[start:end].strip())
        if len(" ".join(windows)) >= MAX_SEED_EXCERPT:
            break
    excerpt = " … ".join(windows) if windows else text
    if len(excerpt) < 400 and text:
        excerpt = f"{excerpt}\n{text}" if excerpt else text
    return replace(combined, text=excerpt[:MAX_SEED_EXCERPT])


def _identity_page_from_candidate(
    candidate: Candidate, page: PageEvidence | None
) -> PageEvidence:
    if page and (page.text or page.title or page.meta_description):
        return page
    text = " ".join(part for part in (candidate.title, candidate.snippet) if part)
    return replace(
        empty_evidence(candidate.normalized_url or candidate.url, title=candidate.title),
        text=text,
        meta_description=candidate.snippet or None,
    )


def attach_seed_provenance(
    row: BusinessCandidate,
    resolution: SeedResolution,
) -> BusinessCandidate:
    evidence = dict(row.evidence or {})
    verified_website = resolution.official_website_verified
    evidence["entity_origin"] = ORIGIN_DIRECT if verified_website else ORIGIN_SEED
    evidence["seed_origin"] = ORIGIN_SEED
    evidence["seed_name"] = resolution.seed.seed_name
    evidence["seed_resolution_path"] = resolution.resolution_path
    evidence["reference_expected_stockist"] = resolution.seed.reference_expected_stockist
    evidence["entity_verified_from"] = resolution.entity_verified_from
    evidence["entity_relationship"] = resolution.entity_relationship
    evidence["identity_type"] = resolution.identity_type
    evidence["identity_sources"] = list(resolution.identity_sources)
    evidence["google_maps_url"] = resolution.google_maps_url
    evidence["identity_signals"] = list(resolution.identity_signals)
    website = row.website if is_usable_official_url(row.website) else resolution.website
    return replace(
        row,
        website=website,
        instagram=row.instagram or resolution.instagram_url,
        facebook=row.facebook or resolution.facebook_url,
        evidence=evidence,
    )


def _identity_row(resolution: SeedResolution, candidate: Candidate) -> BusinessCandidate:
    url = candidate.normalized_url or candidate.url
    if resolution.official_website_verified and resolution.website:
        source_type = ResultType.WEBSITE.value
        source_url = resolution.website
    elif candidate.result_type is ResultType.SOCIAL:
        source_type = ResultType.SOCIAL.value
        source_url = url
    else:
        source_type = "GOOGLE_MAPS" if is_google_maps_url(url) else candidate.result_type.value
        source_url = url
    return BusinessCandidate(
        business_name=resolution.seed.seed_name,
        website=resolution.website if resolution.official_website_verified else None,
        instagram=resolution.instagram_url if resolution.instagram_verified else None,
        facebook=resolution.facebook_url if resolution.facebook_verified else None,
        whatsapp=None,
        phone=None,
        email=None,
        address=None,
        city=resolution.seed.city,
        source_url=source_url,
        source_type=source_type,
        business_type=BusinessType.UNKNOWN,
        fashion_relevance=Relevance.UNKNOWN,
        women_fashion_relevance=Relevance.UNKNOWN,
        physical_store=PhysicalStore.UNKNOWN,
        evidence={"signals": ["seed_identity"], "source_url": source_url},
        confidence=Confidence.MEDIUM,
    )


def classify_resolved_seed(
    *,
    fetcher,
    client: LocalLLMClient,
    resolution: SeedResolution,
) -> tuple[list[BusinessCandidate], dict]:
    """Run identify + Qwen on a resolved seed. Does not use expected stockist."""
    candidate = resolution.candidate
    empty_stats = {
        "qwen_attempted": False,
        "qwen_success": False,
        "validation_rejected": False,
        "rejected": [],
        "enriched_pages": 0,
        "stockist_pages": [],
        "stockist_evidence_available": False,
    }
    if candidate is None:
        return [], empty_stats
    enriched: EnrichedEvidence | None = None
    allow_identity = True
    if candidate.result_type is ResultType.WEBSITE and resolution.official_website_verified:
        enriched = enrich_candidate(fetcher, candidate)
        if enriched.pages:
            add_seed_stockist_pages(fetcher, enriched)
            home = enriched.pages[0]
            signals = extract_business_signals(home)
            rows = identify_business_candidates(
                candidate, home, signals, enriched=enriched
            )
            page_for_llm = seed_page_evidence_for_llm(enriched)
            signals_for_llm = enriched.combined_signals()
        else:
            page = empty_evidence(candidate.normalized_url or candidate.url, title=candidate.title)
            signals = empty_signals()
            rows = identify_business_candidates(candidate, page, signals)
            page_for_llm = page
            signals_for_llm = signals
    else:
        fetch = fetcher.fetch(candidate)
        page = None
        if fetch.fetched and fetch.html:
            page = extract_page_evidence(
                fetch.html,
                source_url=candidate.normalized_url or candidate.url,
                final_url=fetch.final_url,
            )
            signals = extract_business_signals(page)
            rows = identify_business_candidates(candidate, page, signals)
        else:
            signals = empty_signals()
            rows = []
        page_for_llm = _identity_page_from_candidate(candidate, page)
        signals_for_llm = signals
        if not rows:
            rows = [_identity_row(resolution, candidate)]

    attached_rows: list[BusinessCandidate] = []
    attempted = False
    success = False
    rejected: list[str] = []
    for row in rows:
        seeded = attach_seed_provenance(row, resolution)
        attached, result = maybe_classify_with_local_llm(
            client,
            candidate=candidate,
            row=seeded,
            page_evidence=page_for_llm,
            signals=signals_for_llm,
            enriched=enriched,
            allow_official_identity=allow_identity,
        )
        attempted = attempted or result.attempted
        success = success or (
            result.attempted and result.validated is not None and not result.error
        )
        validated = ((attached.evidence or {}).get("ai") or {}).get("validated") or {}
        rejected = list(validated.get("rejected") or rejected)
        attached_rows.append(attached)
    stockist_text = (page_for_llm.text or "") + " " + " ".join(page_for_llm.headings or [])
    return attached_rows, {
        "qwen_attempted": attempted,
        "qwen_success": success,
        "validation_rejected": bool(rejected),
        "rejected": rejected,
        "enriched_pages": len(enriched.pages) if enriched else 0,
        "stockist_pages": list(enriched.selected_urls) if enriched else [],
        "stockist_evidence_available": bool(SEED_EXCERPT_HINT_RE.search(stockist_text)),
    }


def seed_report_row(
    resolution: SeedResolution,
    rows: list[BusinessCandidate],
    stats: dict,
) -> dict:
    from llm_benchmark import stockist_of

    owner = rows[0] if rows else None
    validated = ((owner.evidence or {}).get("ai") or {}).get("validated") if owner else {}
    validated = validated or {}
    rejected = list(validated.get("rejected") or stats.get("rejected") or [])
    evidence = (owner.evidence or {}) if owner else {}
    return {
        "seed_name": resolution.seed.seed_name,
        "city": resolution.seed.city,
        "reference_expected_stockist": resolution.seed.reference_expected_stockist,
        "discovered": resolution.discovered or resolution.identity_found,
        "organic_hit": resolution.organic_hit,
        "identity_found": resolution.identity_found,
        "identity_type": resolution.identity_type,
        "identity_verified": resolution.identity_verified,
        "identity_sources": list(resolution.identity_sources),
        "official_website_found": bool(resolution.website),
        "official_website_verified": resolution.official_website_verified,
        "website": resolution.website,
        "instagram": resolution.instagram_url if resolution.instagram_verified else None,
        "facebook": resolution.facebook_url if resolution.facebook_verified else None,
        "google_maps_url": resolution.google_maps_url,
        "google_business_verified": resolution.google_business_verified,
        "google_business_evidence": bool(resolution.google_business_verified),
        "entity_origin": evidence.get("entity_origin") if owner else ORIGIN_SEED,
        "entity_verified_from": resolution.entity_verified_from,
        "entity_relationship": resolution.entity_relationship,
        "seed_origin": ORIGIN_SEED,
        "resolution_path": resolution.resolution_path,
        "business_identity": owner.business_name if owner else UNKNOWN,
        "physical_store": owner.physical_store.value if owner else UNKNOWN,
        "business_type_rules": owner.business_type.value if owner else UNKNOWN,
        "business_type_qwen": evidence.get("ai_business_type") if owner else None,
        "women_fashion_rules": owner.women_fashion_relevance.value if owner else UNKNOWN,
        "women_fashion_qwen": evidence.get("ai_women_fashion") if owner else None,
        "carries_other_brands": validated.get("carries_other_brands"),
        "potential_stockist": stockist_of(owner, after_ai=True) if owner else "UNKNOWN",
        "confidence": owner.confidence.value if owner else UNKNOWN,
        "ai_confidence": evidence.get("ai_confidence") if owner else None,
        "evidence": list(validated.get("evidence") or []),
        "stockist_evidence_available": stats.get("stockist_evidence_available", False),
        "validation": "rejected" if rejected else ("passed" if stats.get("qwen_success") else "n/a"),
        "rejection_reason": rejected,
        "qwen_reached": stats.get("qwen_attempted", False),
        "enriched_pages": stats.get("enriched_pages", 0),
        "stockist_pages": stats.get("stockist_pages") or [],
        "notes": resolution.notes,
    }


def seed_metrics(rows: list[dict]) -> dict:
    return {
        "seeds_total": len(rows),
        "seeds_found": sum(1 for row in rows if row.get("discovered")),
        "seeds_with_official_website": sum(
            1 for row in rows if row.get("official_website_verified")
        ),
        "seeds_with_official_instagram": sum(1 for row in rows if row.get("instagram")),
        "seeds_with_official_facebook": sum(1 for row in rows if row.get("facebook")),
        "seeds_with_verified_google_business": sum(
            1 for row in rows if row.get("google_business_verified")
        ),
        "seeds_with_any_verified_identity": sum(
            1 for row in rows if row.get("identity_verified")
        ),
        "seeds_with_multiple_identity_sources": sum(
            1 for row in rows if len(row.get("identity_sources") or []) > 1
        ),
        "seeds_reaching_qwen": sum(1 for row in rows if row.get("qwen_reached")),
        "seeds_stockist_yes": sum(1 for row in rows if row.get("potential_stockist") == "YES"),
        "seeds_stockist_no": sum(1 for row in rows if row.get("potential_stockist") == "NO"),
        "seeds_stockist_unknown": sum(
            1 for row in rows if row.get("potential_stockist") == "UNKNOWN"
        ),
        "seeds_validation_rejected": sum(1 for row in rows if row.get("validation") == "rejected"),
        "seeds_without_official_website": sum(
            1 for row in rows if not row.get("official_website_verified")
        ),
        "seeds_organic_hits": sum(1 for row in rows if row.get("organic_hit")),
    }
