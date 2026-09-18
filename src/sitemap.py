"""Bounded sitemap discovery for enrichment URL selection.

Sitemaps are a URL source only. Documents are fetched with the existing
PageFetcher; loc values are filtered and ranked later. No product crawl.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlparse

from url_normalization import extract_domain, normalize_url

MAX_SITEMAP_INDEX_LEVELS = 1
MAX_CHILD_SITEMAPS = 5
MAX_SITEMAP_LOCS = 2_000


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
    if locs:
        return SitemapParse(kind="urlset", locs=locs)
    return SitemapParse(kind="invalid")


def site_origin(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    return f"{scheme}://{netloc}" if netloc else ""


def discover_sitemap_urls(fetcher, root_url: str) -> SitemapDiscovery:
    """Fetch a small number of sitemap documents and collect page loc URLs."""
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
        fetcher, root_url, primary, result, fetched_docs, page_locs, children_left
    )
    if not parsed_ok and robots_seeds:
        parsed_ok = _ingest_sitemap_docs(
            fetcher, root_url, fallbacks, result, fetched_docs, page_locs, children_left
        )

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
) -> bool:
    parsed_ok = False
    for seed, source in seeds:
        if not _same_registrable_site(root_url, seed):
            continue
        parsed = _fetch_and_parse(fetcher, seed, result, fetched_docs)
        if parsed is None:
            continue
        parsed_ok = True
        if result.source is None:
            result.source = source
        if parsed.kind == "urlset":
            page_locs.extend(parsed.locs)
        elif parsed.kind == "index":
            for child in parsed.locs:
                if children_left[0] <= 0:
                    break
                if not _same_registrable_site(root_url, child):
                    continue
                child_parsed = _fetch_and_parse(fetcher, child, result, fetched_docs)
                if child_parsed is None:
                    continue
                children_left[0] -= 1
                result.child_sitemaps_fetched += 1
                if child_parsed.kind == "urlset":
                    page_locs.extend(child_parsed.locs)
                # Nested indexes are ignored (max index depth = 1).
        if parsed.kind == "urlset":
            break
        if parsed.kind == "index":
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
    root = extract_domain(root_url)
    link = extract_domain(other)
    if not root or not link:
        return False
    return link == root or link.endswith("." + root)


def _local_name(tag: str) -> str:
    return (tag or "").split("}", 1)[-1].lower()
