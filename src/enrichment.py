"""Depth-1 same-site enrichment for WEBSITE candidates.

Uses homepage links and bounded sitemap discovery to choose at most three
Contact / Stores / About pages. It does not recursively follow page links,
accept external evidence, or bypass robots.txt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from candidates import Candidate
from classification import (
    DIRECTORY_DOMAINS,
    SOCIAL_DOMAINS,
    VIDEO_DOMAINS,
    ResultType,
    _host_matches,
)
from content_extraction import (
    CITY_NAMES,
    MAX_HEADINGS,
    MAX_LINKS,
    MAX_SIGNAL_ITEMS,
    MAX_TEXT_CHARS,
    BusinessSignals,
    ExtractedLink,
    PageEvidence,
    empty_evidence,
    extract_business_signals,
    extract_page_evidence,
)
from fetcher import FetchResult, PageFetcher
from sitemap import SitemapDiscovery, discover_sitemap_urls
from url_normalization import extract_domain, is_same_site as _is_same_site, normalize_url

MAX_ENRICHMENT_PAGES = 3
MAX_TOTAL_PAGES = 4  # homepage + enrichment

CONTACT_TOKENS = (
    "contact-us",
    "contact_us",
    "get-in-touch",
    "getintouch",
    "reach-us",
    "reachus",
    "contact",
)
LOCATION_TOKENS = (
    "store-locator",
    "storelocator",
    "showrooms",
    "showroom",
    "locations",
    "location",
    "visit-us",
    "visitus",
    "find-us",
    "findus",
    "stores",
    "store",
)
STORE_TOKENS = (
    "store-locator",
    "storelocator",
    "stores",
    "store",
)
LOCATION_PLACE_TOKENS = (
    "locations",
    "location",
)
VISIT_TOKENS = (
    "showrooms",
    "showroom",
    "visit-us",
    "visitus",
    "find-us",
    "findus",
)
ABOUT_TOKENS = (
    "about-us",
    "aboutus",
    "our-story",
    "ourstory",
    "who-we-are",
    "whoweare",
    "about",
    "story",
)
BUSINESS_TOKENS = (
    "boutique",
    "boutiques",
    "retail",
    "stockist",
    "stockists",
)
REJECT_PATH_TOKENS = (
    "/login",
    "/signin",
    "/signup",
    "/register",
    "/cart",
    "/checkout",
    "/account",
    "/wishlist",
    "/search",
    "/privacy",
    "/terms",
    "/shipping",
    "/returns",
    "/refund",
    "/products/",
    "/product/",
    "/collections/",
    "/collection/",
    "/categories/",
    "/category/",
    "/item/",
    "/items/",
    "/article/",
    "/articles/",
    "/blog/",
    "/blogs/",
)
REJECT_PATH_SEGMENTS = {
    "login",
    "signin",
    "signup",
    "register",
    "cart",
    "checkout",
    "account",
    "wishlist",
    "search",
    "privacy",
    "terms",
    "shipping",
    "returns",
    "refund",
    "products",
    "product",
    "collections",
    "collection",
    "categories",
    "category",
    "item",
    "items",
    "article",
    "articles",
    "blog",
    "blogs",
    "p",
}
REJECT_ANCHORS = {
    "home",
    "homepage",
    "login",
    "log in",
    "sign in",
    "sign up",
    "register",
    "cart",
    "checkout",
    "account",
    "my account",
    "wishlist",
    "search",
    "privacy",
    "privacy policy",
    "terms",
    "shipping",
    "returns",
    "instagram",
    "facebook",
    "youtube",
    "whatsapp",
    "twitter",
    "pinterest",
}


@dataclass
class EnrichedEvidence:
    """Homepage plus optional same-domain Contact/Store/About pages."""

    root_url: str
    pages: list[PageEvidence] = field(default_factory=list)
    attempted_urls: list[str] = field(default_factory=list)
    successful_urls: list[str] = field(default_factory=list)
    failed_urls: list[str] = field(default_factory=list)
    selected_urls: list[str] = field(default_factory=list)
    selected_url_sources: list[dict[str, str]] = field(default_factory=list)
    sitemap_discovered: bool = False
    sitemap_source: str | None = None
    sitemap_url_count: int = 0
    relevant_sitemap_urls: list[str] = field(default_factory=list)

    def combined_titles(self) -> list[str]:
        return [page.title for page in self.pages if page.title]

    def combined_meta_descriptions(self) -> list[str]:
        return [page.meta_description for page in self.pages if page.meta_description]

    def combined_headings(self) -> list[str]:
        headings: list[str] = []
        for page in self.pages:
            headings.extend(page.headings)
        return headings[: MAX_HEADINGS * MAX_TOTAL_PAGES]

    def combined_links(self) -> list[ExtractedLink]:
        seen: set[str] = set()
        links: list[ExtractedLink] = []
        for page in self.pages:
            for link in page.links:
                key = link.normalized_url or link.url
                if key in seen:
                    continue
                seen.add(key)
                links.append(link)
                if len(links) >= MAX_LINKS * 2:
                    return links
        return links

    def combined_text(self) -> str:
        chunks = [self._page_text(page) for page in self._pages_by_priority()]
        text = "\n\n".join(chunk for chunk in chunks if chunk)
        limit = MAX_TEXT_CHARS * MAX_TOTAL_PAGES
        return text[:limit]

    def as_page_evidence(self) -> PageEvidence:
        """Single PageEvidence for identification, location pages first."""
        ordered = self._pages_by_priority()
        if not ordered:
            return empty_evidence(self.root_url)
        # enrich_candidate always stores the homepage first. Do not let a lower
        # priority /shop page become the canonical website URL.
        home = self.pages[0]
        title = next((page.title for page in ordered if page.title), None)
        meta = next((page.meta_description for page in ordered if page.meta_description), None)
        return PageEvidence(
            source_url=self.root_url,
            final_url=home.final_url if home else self.root_url,
            domain=extract_domain(self.root_url),
            title=title,
            meta_description=meta,
            text=self.combined_text(),
            headings=self.combined_headings(),
            links=self.combined_links(),
        )

    def combined_signals(self) -> BusinessSignals:
        emails: list[str] = []
        phones: list[str] = []
        social: list[str] = []
        whatsapp: list[str] = []
        addresses: list[str] = []
        cities: list[str] = []
        for page in self._pages_by_priority():
            signals = extract_business_signals(page)
            emails.extend(signals.emails)
            phones.extend(signals.phones)
            social.extend(signals.social_urls)
            whatsapp.extend(signals.whatsapp_urls)
            addresses.extend(signals.address_candidates)
            cities.extend(signals.city_mentions)
        return BusinessSignals(
            emails=_unique(emails)[:MAX_SIGNAL_ITEMS],
            phones=_unique(phones)[:MAX_SIGNAL_ITEMS],
            social_urls=_unique(social)[:MAX_SIGNAL_ITEMS],
            whatsapp_urls=_unique(whatsapp)[:MAX_SIGNAL_ITEMS],
            address_candidates=_unique(addresses)[:MAX_SIGNAL_ITEMS],
            city_mentions=_unique(cities)[:MAX_SIGNAL_ITEMS],
        )

    def address_sources(self) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        for page in self._pages_by_priority():
            signals = extract_business_signals(page)
            page_url = page.final_url or page.source_url
            role = classify_internal_page(page_url, root_url=self.root_url)
            for address in signals.address_candidates:
                found.append({"address": address, "url": page_url, "role": role})
        return found

    def _pages_by_priority(self) -> list[PageEvidence]:
        rank = {"contact": 0, "location": 1, "about": 2, "business": 3, "homepage": 4, "other": 5}

        def key(page: PageEvidence) -> int:
            url = page.final_url or page.source_url
            return rank.get(classify_internal_page(url, root_url=self.root_url), 5)

        return sorted(self.pages, key=key)

    @staticmethod
    def _page_text(page: PageEvidence) -> str:
        return page.text or ""


def is_same_site(root_url: str, link_url: str) -> bool:
    """Backward-compatible public wrapper around URL site comparison."""
    return _is_same_site(root_url, link_url)


def classify_internal_page(url: str, root_url: str | None = None) -> str:
    if root_url:
        root_norm = normalize_url(root_url)
        link_norm = normalize_url(url)
        if root_norm and link_norm and root_norm == link_norm:
            return "homepage"
        if _is_homepage_path(url) and extract_domain(url) == extract_domain(root_url or url):
            return "homepage"
    blob = _path_and_query(url)
    if _token_in(blob, CONTACT_TOKENS):
        return "contact"
    if _token_in(blob, LOCATION_TOKENS):
        return "location"
    if _token_in(blob, ABOUT_TOKENS):
        return "about"
    if _token_in(blob, BUSINESS_TOKENS):
        return "business"
    return "other"


@dataclass(frozen=True)
class EnrichmentCandidate:
    url: str
    score: int
    source: str


def enrichment_priority(link: ExtractedLink, root_url: str) -> int:
    """Higher is better. 0 means do not follow."""
    target = link.normalized_url or normalize_url(link.url) or link.url
    return url_enrichment_score(target, root_url, link.anchor_text)


def url_enrichment_score(url: str, root_url: str, anchor: str = "") -> int:
    if not _usable_enrichment_url(url, root_url, anchor):
        return 0
    root_city = _city_in_url(root_url)
    target_city = _city_in_url(url)
    if root_city and target_city and root_city != target_city:
        return 0
    blob = f"{_path_and_query(url)} {anchor or ''}".lower()
    if (
        root_city
        and not target_city
        and _token_in(blob, LOCATION_TOKENS)
        and not _is_generic_location_url(url)
    ):
        return 0
    if _token_in(blob, CONTACT_TOKENS):
        return 100
    if _token_in(blob, STORE_TOKENS):
        return 94
    if _token_in(blob, LOCATION_PLACE_TOKENS):
        return 92
    if _token_in(blob, VISIT_TOKENS):
        return 90
    if _token_in(blob, ABOUT_TOKENS):
        return 70
    if _token_in(blob, BUSINESS_TOKENS):
        return 55
    if re.search(r"\bshop\b", blob) and not re.search(
        r"\b(dress|dresses|top|tops|sale|new|product)\b", blob
    ):
        return 20
    return 0


SOURCE_RANK = {
    "homepage_link": 0,
    "robots_sitemap": 1,
    "sitemap": 2,
    "sitemap_index": 2,
}


def select_enrichment_urls(
    links: list[ExtractedLink],
    root_url: str,
    *,
    limit: int = MAX_ENRICHMENT_PAGES,
    sitemap_urls: list[str] | None = None,
    sitemap_source: str = "sitemap",
) -> list[str]:
    """Pick up to ``limit`` unique internal Contact/Store/About URLs."""
    chosen = select_enrichment_candidates(
        links,
        root_url,
        limit=limit,
        sitemap_urls=sitemap_urls,
        sitemap_source=sitemap_source,
    )
    return [item.url for item in chosen]


def select_enrichment_candidates(
    links: list[ExtractedLink],
    root_url: str,
    *,
    limit: int = MAX_ENRICHMENT_PAGES,
    sitemap_urls: list[str] | None = None,
    sitemap_source: str = "sitemap",
) -> list[EnrichmentCandidate]:
    """Rank navigation links ahead of equivalent sitemap URLs."""
    ranked: list[EnrichmentCandidate] = []
    seen: set[str] = set()
    root_norm = normalize_url(root_url)
    if root_norm:
        seen.add(root_norm)

    def consider(url: str | None, score: int, source: str) -> None:
        if score <= 0 or not url or url in seen:
            return
        seen.add(url)
        ranked.append(EnrichmentCandidate(url=url, score=score, source=source))

    for link in links:
        target = link.normalized_url or normalize_url(link.url)
        consider(target, enrichment_priority(link, root_url), "homepage_link")
    for url in sitemap_urls or []:
        target = normalize_url(url) or url.rstrip("/")
        consider(
            target,
            url_enrichment_score(target, root_url, ""),
            sitemap_source,
        )

    ranked.sort(
        key=lambda item: (
            -item.score,
            SOURCE_RANK.get(item.source, 9),
            item.url,
        )
    )
    high = [item for item in ranked if item.score >= 70]
    chosen = high[:limit]
    if len(chosen) < limit:
        for item in ranked:
            if item in chosen:
                continue
            chosen.append(item)
            if len(chosen) >= limit:
                break
    return chosen[:limit]


def enrich_candidate(fetcher: PageFetcher, candidate: Candidate) -> EnrichedEvidence:
    """Fetch homepage + up to 3 internal pages. Failures are recorded, not raised."""
    root = (candidate.normalized_url or candidate.url or "").strip()
    result = EnrichedEvidence(root_url=root)
    if candidate.result_type is not ResultType.WEBSITE:
        return result
    parsed = urlparse(root if "://" in root else f"https://{root}")
    if parsed.scheme not in {"http", "https"}:
        return result

    homepage = _fetch_page(fetcher, root)
    result.attempted_urls.append(root)
    if homepage is None:
        result.failed_urls.append(root)
        return result
    result.successful_urls.append(homepage.final_url or root)
    result.pages.append(homepage)

    effective_root = homepage.final_url or root
    sitemap: SitemapDiscovery = discover_sitemap_urls(fetcher, effective_root)
    result.sitemap_discovered = sitemap.discovered
    result.sitemap_source = sitemap.source
    result.sitemap_url_count = len(sitemap.urls)
    relevant = [
        url
        for url in sitemap.urls
        if url_enrichment_score(url, effective_root, "") > 0
    ]
    result.relevant_sitemap_urls = relevant[:50]
    sitemap_source = sitemap.source or "sitemap"
    selected = select_enrichment_candidates(
        homepage.links,
        effective_root,
        sitemap_urls=sitemap.urls,
        sitemap_source=sitemap_source,
    )
    result.selected_urls = [item.url for item in selected]
    result.selected_url_sources = [
        {"url": item.url, "source": item.source} for item in selected
    ]
    for item in selected:
        if len(result.pages) >= MAX_TOTAL_PAGES:
            break
        if item.url in result.attempted_urls:
            continue
        result.attempted_urls.append(item.url)
        page = _fetch_page(fetcher, item.url, required_site=effective_root)
        if page is None:
            result.failed_urls.append(item.url)
            continue
        result.successful_urls.append(page.final_url or item.url)
        result.pages.append(page)
    return result


def _fetch_page(
    fetcher: PageFetcher,
    url: str,
    *,
    required_site: str | None = None,
) -> PageEvidence | None:
    fetch: FetchResult = fetcher.fetch(url)
    if not fetch.fetched or not fetch.html:
        return None
    if required_site and not is_same_site(required_site, fetch.final_url or url):
        return None
    return extract_page_evidence(
        fetch.html,
        source_url=url,
        final_url=fetch.final_url,
    )


def _usable_enrichment_url(url: str, root_url: str, anchor: str) -> bool:
    if not url or not is_same_site(root_url, url):
        return False
    domain = extract_domain(url)
    if _host_matches(domain, SOCIAL_DOMAINS | VIDEO_DOMAINS | DIRECTORY_DOMAINS):
        return False
    if domain in {"google.com", "maps.google.com", "wa.me", "whatsapp.com"}:
        return False
    parsed = urlparse(url)
    if parsed.query:
        return False
    path = (parsed.path or "").lower()
    if path.endswith(
        (
            ".xml",
            ".xml.gz",
            ".gz",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".svg",
            ".pdf",
            ".css",
            ".js",
        )
    ):
        return False
    if _is_homepage_path(url):
        return False
    if any(token in path for token in REJECT_PATH_TOKENS):
        return False
    segments = [part for part in path.split("/") if part]
    if any(not unquote(part).strip() for part in segments):
        return False
    if any(part in REJECT_PATH_SEGMENTS for part in segments):
        return False
    if (anchor or "").strip().lower() in REJECT_ANCHORS:
        return False
    root_norm = normalize_url(root_url)
    link_norm = normalize_url(url)
    if root_norm and link_norm and root_norm == link_norm:
        return False
    return True


def _path_and_query(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.path or ''} {parsed.query or ''}".lower().replace("_", "-")


def _city_in_url(url: str) -> str | None:
    path = re.sub(r"[-_/]+", " ", urlparse(url).path).lower()
    for city in sorted(CITY_NAMES, key=len, reverse=True):
        if city in {"Bandra", "Khar", "Colaba"}:
            continue
        if re.search(rf"\b{re.escape(city.lower())}\b", path):
            return city
    return None


def _is_generic_location_url(url: str) -> bool:
    generic = {
        "pages",
        "store",
        "stores",
        "our-store",
        "our-stores",
        "location",
        "locations",
        "showroom",
        "showrooms",
        "store-locator",
        "storelocator",
        "visit-us",
        "find-us",
        "reach-us",
        "reachus",
    }
    segments = [
        unquote(part).strip().lower()
        for part in urlparse(url).path.split("/")
        if part
    ]
    return bool(segments) and all(part in generic for part in segments)


def _token_in(blob: str, tokens: tuple[str, ...]) -> bool:
    compact = blob.lower().replace("_", "-")
    for token in tokens:
        if re.search(
            rf"(^|[^a-z0-9]){re.escape(token)}([^a-z0-9]|$)",
            compact,
        ):
            return True
    return False


def _is_homepage_path(url: str) -> bool:
    path = (urlparse(url).path or "").strip("/")
    return path == "" or path.lower() in {"index", "index.html", "home", "homepage"}


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered
