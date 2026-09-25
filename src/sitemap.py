"""Bounded sitemap discovery for enrichment URL selection.

Sitemaps are a URL source only. Documents are fetched with the existing
PageFetcher; loc values are filtered and ranked later. No product crawl.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse

from url_normalization import extract_domain, is_same_site, normalize_url

MAX_SITEMAP_INDEX_LEVELS = 1
MAX_CHILD_SITEMAPS = 5
MAX_SITEMAP_LOCS = 2_000
MIN_RELEVANT_LOCS_TO_STOP = 3

RELEVANT_LOC_TOKENS = (
    "contact",
    "about",
    "store",
    "stores",
    "location",
    "locations",
    "find-us",
    "findus",
    "visit",
    "visit-us",
    "shop",
    "boutique",
    "showroom",
    "pages",
    "our-story",
)
SKIP_CHILD_TOKENS = (
    "product",
    "collection",
    "category",
    "image",
    "asset",
    "blog",
    "post",
)


@dataclass
class SitemapParse:
    kind: str  # urlset | index | invalid
    locs: list[str] = field(default_factory=list)


@dataclass
class SitemapDiscovery:
    discovered: bool = False
    source: str | None = None
    sitemap_documents: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    child_sitemaps_fetched: int = 0


def parse_robots_sitemaps(text: str) -> list[str]:
    found: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.lower().startswith("sitemap:"):
            value = line.split(":", 1)[1].strip()
            if value:
                found.append(value)
    return found


def parse_sitemap_xml(text: str) -> SitemapParse:
    """Extract ``<loc>`` values. Invalid or empty XML yields no URLs."""
    raw = (text or "").lstrip("\ufeff").strip()
    if not raw or "<" not in raw:
        return SitemapParse(kind="invalid")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return SitemapParse(kind="invalid")
    except Exception:
        return SitemapParse(kind="invalid")
    tag = _local_name(root.tag)
    locs: list[str] = []
    for element in root.iter():
        if _local_name(element.tag) != "loc":
            continue
        value = (element.text or "").strip()
        if value:
            locs.append(value)
        if len(locs) >= MAX_SITEMAP_LOCS:
            break
    if tag == "sitemapindex":
        return SitemapParse(kind="index", locs=locs)
    if tag == "urlset":
        return SitemapParse(kind="urlset", locs=locs)
    return SitemapParse(kind="invalid")


def site_origin(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    return f"{scheme}://{netloc}" if netloc else ""


def discover_sitemap_urls(fetcher, root_url: str) -> SitemapDiscovery:
    """Fetch a small number of sitemap documents and collect page loc URLs."""
    cache = getattr(fetcher, "_sitemap_discovery_cache", None)
    if cache is None:
        cache = {}
        try:
            fetcher._sitemap_discovery_cache = cache
        except Exception:
            cache = {}
    key = extract_domain(root_url) or site_origin(root_url)
    if key and key in cache:
        return cache[key]
    result = _discover_sitemap_urls_uncached(fetcher, root_url)
    if key:
        cache[key] = result
    return result


def _discover_sitemap_urls_uncached(fetcher, root_url: str) -> SitemapDiscovery:
    result = SitemapDiscovery()
    origin = site_origin(root_url)
    if not origin:
        return result

    robots_seeds = _robots_sitemap_seeds(fetcher, root_url)
    fallbacks = [
        (f"{origin}/sitemap.xml", "sitemap"),
        (f"{origin}/sitemap_index.xml", "sitemap_index"),
    ]
    page_locs: list[str] = []
    fetched_docs: set[str] = set()
    children_left = [MAX_CHILD_SITEMAPS]
    primary = robots_seeds[:3] if robots_seeds else fallbacks
    parsed_ok = _ingest_sitemap_docs(
        fetcher,
        root_url,
        primary,
        result,
        fetched_docs,
        page_locs,
        children_left,
        stop_after_locs=not bool(robots_seeds),
    )
    if not page_locs and robots_seeds:
        parsed_ok = _ingest_sitemap_docs(
            fetcher,
            root_url,
            fallbacks,
            result,
            fetched_docs,
            page_locs,
            children_left,
            stop_after_locs=True,
        ) or parsed_ok

    if not parsed_ok:
        return result

    seen: set[str] = set()
    for loc in page_locs:
        if not _same_registrable_site(root_url, loc):
            continue
        normalized = normalize_url(loc) or loc.rstrip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.urls.append(normalized)
        if len(result.urls) >= MAX_SITEMAP_LOCS:
            break
    result.discovered = bool(result.urls) or parsed_ok
    return result


def _robots_sitemap_seeds(fetcher, root_url: str) -> list[tuple[str, str]]:
    robots_fn = getattr(fetcher, "robots_sitemap_urls", None)
    listed: list[str] = []
    if callable(robots_fn):
        try:
            listed = list(robots_fn(root_url) or [])
        except Exception:
            listed = []
    seeds: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url in listed:
        if not _same_registrable_site(root_url, url):
            continue
        key = normalize_url(url) or url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        seeds.append((url, "robots_sitemap"))
    return seeds


def _ingest_sitemap_docs(
    fetcher,
    root_url: str,
    seeds: list[tuple[str, str]],
    result: SitemapDiscovery,
    fetched_docs: set[str],
    page_locs: list[str],
    children_left: list[int],
    *,
    stop_after_locs: bool,
) -> bool:
    parsed_ok = False
    for seed, source in seeds:
        if not _same_registrable_site(root_url, seed):
            continue
        if _enough_relevant_locs(page_locs):
            break
        parsed = _fetch_and_parse(fetcher, seed, result, fetched_docs)
        if parsed is None:
            continue
        parsed_ok = True
        if result.source is None:
            result.source = source
        if parsed.kind == "urlset":
            page_locs.extend(parsed.locs)
        elif parsed.kind == "index" and MAX_SITEMAP_INDEX_LEVELS >= 1:
            children = sorted(parsed.locs, key=_child_sitemap_priority, reverse=True)
            for child in children:
                if children_left[0] <= 0:
                    break
                if _enough_relevant_locs(page_locs):
                    break
                priority = _child_sitemap_priority(child)
                relevant_so_far = _relevant_loc_count(page_locs)
                if priority <= 0 and relevant_so_far > 0:
                    continue
                if priority <= 1 and relevant_so_far >= 1:
                    continue
                if not _same_registrable_site(root_url, child):
                    continue
                # Every attempted child consumes the budget, including 404,
                # timeout, and malformed XML responses.
                children_left[0] -= 1
                result.child_sitemaps_fetched += 1
                child_parsed = _fetch_and_parse(fetcher, child, result, fetched_docs)
                if child_parsed is None:
                    continue
                if child_parsed.kind == "urlset":
                    page_locs.extend(child_parsed.locs)
                # Nested indexes are ignored (max index depth = 1).
        if stop_after_locs and parsed.kind == "urlset" and parsed.locs:
            break
        if stop_after_locs and parsed.kind == "index":
            break
        if _enough_relevant_locs(page_locs):
            break
    return parsed_ok


def _fetch_and_parse(fetcher, url: str, result: SitemapDiscovery, fetched: set[str]) -> SitemapParse | None:
    key = normalize_url(url) or url.rstrip("/")
    if key in fetched:
        return None
    fetched.add(key)
    try:
        fetch = fetcher.fetch(url, allow_xml=True)
    except TypeError:
        fetch = fetcher.fetch(url)
    result.sitemap_documents.append(url)
    if not getattr(fetch, "fetched", False) or not getattr(fetch, "html", None):
        return None
    parsed = parse_sitemap_xml(fetch.html)
    if parsed.kind == "invalid":
        return None
    return parsed


def _same_registrable_site(root_url: str, other: str) -> bool:
    return is_same_site(root_url, other)


def _local_name(tag: str) -> str:
    return (tag or "").split("}", 1)[-1].lower()


def _looks_relevant_loc(url: str) -> bool:
    path = urlparse(url).path.lower()
    if any(token in path for token in ("/product", "/collection", "/category", "/item")):
        return False
    return any(token in path for token in RELEVANT_LOC_TOKENS)


def _relevant_loc_count(locs: list[str]) -> int:
    seen: set[str] = set()
    count = 0
    for loc in locs:
        key = normalize_url(loc) or loc
        if key in seen:
            continue
        seen.add(key)
        if _looks_relevant_loc(loc):
            count += 1
    return count


def _enough_relevant_locs(locs: list[str]) -> bool:
    return _relevant_loc_count(locs) >= MIN_RELEVANT_LOCS_TO_STOP


def _child_sitemap_priority(url: str) -> int:
    blob = url.lower()
    if any(token in blob for token in SKIP_CHILD_TOKENS):
        return 0
    score = 0
    for token in ("page", "contact", "store", "location", "about", "static"):
        if token in blob:
            score += 10
    return score if score else 1
