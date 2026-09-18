"""Offline tests for depth-1 same-domain enrichment."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from business_candidates import (
    UNKNOWN,
    PhysicalStore,
    identify_business_candidates,
)
from candidates import Candidate
from classification import ResultType
from content_extraction import ExtractedLink, extract_page_evidence
from enrichment import (
    EnrichedEvidence,
    enrich_candidate,
    is_same_site,
    select_enrichment_candidates,
    select_enrichment_urls,
    url_enrichment_score,
)
from fetcher import FetchResult
from sitemap import discover_sitemap_urls, parse_robots_sitemaps, parse_sitemap_xml


def _candidate(**kwargs) -> Candidate:
    defaults = dict(
        title="Rozina",
        url="https://rozina.example/",
        normalized_url="https://rozina.example",
        domain="rozina.example",
        snippet="",
        result_type=ResultType.WEBSITE,
        search_query="test",
        search_source="test",
    )
    defaults.update(kwargs)
    return Candidate(**defaults)


def _link(url: str, anchor: str, *, external: bool = False) -> ExtractedLink:
    return ExtractedLink(
        url=url,
        normalized_url=url.rstrip("/"),
        anchor_text=anchor,
        useful=True,
        external=external,
    )


def _ok(url: str, html: str, *, content_type: str = "text/html") -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type=content_type,
        html=html,
        error=None,
        fetched=True,
        elapsed_seconds=0.01,
    )


def _fail(url: str, error: str = "HTTP 403") -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=403,
        content_type="text/html",
        html=None,
        error=error,
        fetched=False,
        elapsed_seconds=0.01,
    )


class FakeFetcher:
    def __init__(
        self,
        responses: dict[str, FetchResult],
        *,
        robots_sitemaps: list[str] | None = None,
    ) -> None:
        self.responses = {_lookup_key(key): value for key, value in responses.items()}
        self.fetched_urls: list[str] = []
        self.robots_sitemaps = list(robots_sitemaps or [])

    def fetch(self, target: Candidate | str, *, allow_xml: bool = False) -> FetchResult:
        from url_normalization import normalize_url

        url = target if isinstance(target, str) else (target.normalized_url or target.url)
        key = _lookup_key(url)
        self.fetched_urls.append(key)
        if key in self.responses:
            return self.responses[key]
        normalized = normalize_url(url)
        if normalized and _lookup_key(normalized) in self.responses:
            return self.responses[_lookup_key(normalized)]
        return _fail(url, "missing fake")

    def robots_sitemap_urls(self, _page_url: str) -> list[str]:
        return list(self.robots_sitemaps)


def _lookup_key(url: str) -> str:
    from url_normalization import normalize_url

    return (normalize_url(url) or url.rstrip("/")).rstrip("/")


HOME_HTML = """
<html><head><title>Home</title></head>
<body>
<a href="https://rozina.example/contact">Contact Us</a>
<a href="https://rozina.example/stores">Visit Our Store</a>
<a href="https://rozina.example/about">Our Story</a>
<a href="https://rozina.example/shop">Boutique</a>
<a href="https://instagram.com/rozina">Instagram</a>
<a href="https://rozina.example/cart">Cart</a>
<a href="https://other.example/contact">Other</a>
</body></html>
"""

ABOUT_HTML = """
<html><head><title>Rozina | About Us</title></head>
<body><h1>Rozina</h1><p>Women's clothing boutique.</p>
<a href="https://rozina.example/secret-second">Do not follow</a>
</body></html>
"""

CONTACT_HTML = """
<html><head><title>Contact</title></head>
<body><p>Email hello@rozina.example call +91 98765 43210</p></body></html>
"""

STORE_HTML = """
<html><head><title>Stores</title></head>
<body>
<p>Visit us at 12 Linking Road, Bandra West, Mumbai 400050. Showroom open daily.</p>
</body></html>
"""


class EnrichmentHelperTests(unittest.TestCase):
    def test_internal_links_recognized(self) -> None:
        self.assertTrue(
            is_same_site("https://example.com/shop", "https://www.example.com/contact")
        )

    def test_external_links_rejected(self) -> None:
        self.assertFalse(
            is_same_site("https://example.com/", "https://instagram.com/x")
        )
        selected = select_enrichment_urls(
            [_link("https://instagram.com/x", "Instagram", external=True)],
            "https://rozina.example",
        )
        self.assertEqual(selected, [])

    def test_contact_pages_prioritized(self) -> None:
        selected = select_enrichment_urls(
            [
                _link("https://rozina.example/about", "About"),
                _link("https://rozina.example/contact", "Contact Us"),
            ],
            "https://rozina.example",
        )
        self.assertEqual(selected[0], "https://rozina.example/contact")

    def test_store_pages_prioritized(self) -> None:
        selected = select_enrichment_urls(
            [
                _link("https://rozina.example/about", "Our Story"),
                _link("https://rozina.example/stores", "Visit Our Store"),
            ],
            "https://rozina.example",
        )
        self.assertEqual(selected[0], "https://rozina.example/stores")

    def test_about_pages_prioritized(self) -> None:
        selected = select_enrichment_urls(
            [
                _link("https://rozina.example/shop", "Boutique"),
                _link("https://rozina.example/about", "Our Story"),
            ],
            "https://rozina.example",
        )
        self.assertEqual(selected[0], "https://rozina.example/about")

    def test_ecommerce_navigation_rejected(self) -> None:
        selected = select_enrichment_urls(
            [
                _link("https://rozina.example/cart", "Cart"),
                _link("https://rozina.example/checkout", "Checkout"),
                _link("https://rozina.example/login", "Login"),
                _link("https://rozina.example/products/dress", "Shop Dresses"),
            ],
            "https://rozina.example",
        )
        self.assertEqual(selected, [])


class EnrichmentFetchTests(unittest.TestCase):
    def test_max_three_enrichment_pages_and_homepage_included(self) -> None:
        html = """
        <html><title>Home</title><body>
        <a href="/contact">Contact</a>
        <a href="/stores">Stores</a>
        <a href="/about">About</a>
        <a href="/visit-us">Find Us</a>
        </body></html>
        """
        fetcher = FakeFetcher(
            {
                "https://rozina.example": _ok("https://rozina.example", html),
                "https://rozina.example/contact": _ok(
                    "https://rozina.example/contact", CONTACT_HTML
                ),
                "https://rozina.example/stores": _ok(
                    "https://rozina.example/stores", STORE_HTML
                ),
                "https://rozina.example/about": _ok(
                    "https://rozina.example/about", ABOUT_HTML
                ),
                "https://rozina.example/visit-us": _ok(
                    "https://rozina.example/visit-us", STORE_HTML
                ),
            }
        )
        result = enrich_candidate(fetcher, _candidate())
        self.assertGreaterEqual(len(result.pages), 1)
        self.assertLessEqual(len(result.selected_urls), 3)
        self.assertLessEqual(len(result.pages), 4)
        home_keys = {_lookup_key(url) for url in result.successful_urls}
        self.assertIn("https://rozina.example", home_keys)
        extra = [
            url
            for url in result.successful_urls
            if _lookup_key(url) != "https://rozina.example"
        ]
        self.assertLessEqual(len(extra), 3)
        self.assertLessEqual(len(result.selected_urls), 3)

    def test_duplicate_urls_not_fetched_twice(self) -> None:
        html = """
        <html><title>Home</title><body>
        <a href="https://rozina.example/contact">Contact</a>
        <a href="https://rozina.example/contact/">Contact Us</a>
        <a href="https://www.rozina.example/contact">Contact</a>
        </body></html>
        """
        fetcher = FakeFetcher(
            {
                "https://rozina.example": _ok("https://rozina.example", html),
                "https://rozina.example/contact": _ok(
                    "https://rozina.example/contact", CONTACT_HTML
                ),
            }
        )
        result = enrich_candidate(fetcher, _candidate())
        contact_fetches = [
            url for url in fetcher.fetched_urls if "contact" in url
        ]
        self.assertEqual(len(contact_fetches), 1)
        self.assertEqual(len(result.selected_urls), 1)

    def test_failed_enrichment_page_does_not_fail_candidate(self) -> None:
        fetcher = FakeFetcher(
            {
                "https://rozina.example": _ok("https://rozina.example", HOME_HTML),
                "https://rozina.example/contact": _fail("https://rozina.example/contact"),
                "https://rozina.example/stores": _ok(
                    "https://rozina.example/stores", STORE_HTML
                ),
                "https://rozina.example/about": _ok(
                    "https://rozina.example/about", ABOUT_HTML
                ),
                "https://rozina.example/shop": _ok(
                    "https://rozina.example/shop", ABOUT_HTML
                ),
            }
        )
        result = enrich_candidate(fetcher, _candidate())
        self.assertTrue(result.pages)
        self.assertIn("https://rozina.example/contact", result.failed_urls)
        self.assertGreaterEqual(len(result.successful_urls), 2)

    def test_no_second_level_crawling(self) -> None:
        fetcher = FakeFetcher(
            {
                "https://rozina.example": _ok("https://rozina.example", HOME_HTML),
                "https://rozina.example/contact": _ok(
                    "https://rozina.example/contact", CONTACT_HTML
                ),
                "https://rozina.example/stores": _ok(
                    "https://rozina.example/stores", STORE_HTML
                ),
                "https://rozina.example/about": _ok(
                    "https://rozina.example/about", ABOUT_HTML
                ),
                "https://rozina.example/shop": _ok(
                    "https://rozina.example/shop", ABOUT_HTML
                ),
                "https://rozina.example/secret-second": _ok(
                    "https://rozina.example/secret-second", "<html></html>"
                ),
            }
        )
        enrich_candidate(fetcher, _candidate())
        self.assertFalse(any("secret-second" in url for url in fetcher.fetched_urls))


class EnrichmentIdentificationTests(unittest.TestCase):
    def test_about_name_beats_generic_homepage(self) -> None:
        home = extract_page_evidence(
            '<html><head><title>Home</title></head><body><a href="/about">About</a></body></html>',
            source_url="https://rozina.example",
            final_url="https://rozina.example",
        )
        about = extract_page_evidence(
            ABOUT_HTML,
            source_url="https://rozina.example/about",
            final_url="https://rozina.example/about",
        )
        enriched = EnrichedEvidence(
            root_url="https://rozina.example",
            pages=[home, about],
            successful_urls=["https://rozina.example", "https://rozina.example/about"],
            selected_urls=["https://rozina.example/about"],
        )
        rows = identify_business_candidates(
            _candidate(),
            home,
            extract_signals(home),
            enriched=enriched,
        )
        self.assertEqual(rows[0].business_name, "Rozina")
        self.assertIn("about", rows[0].evidence.get("name_source", ""))

    def test_ecommerce_chrome_rejected_as_name(self) -> None:
        home = extract_page_evidence(
            "<html><head><title>Item added to your cart</title></head>"
            "<body><p>Women's clothing boutique.</p></body></html>",
            source_url="https://rozina.example",
            final_url="https://rozina.example",
        )
        rows = identify_business_candidates(
            _candidate(title="Item added to your cart"),
            home,
            extract_signals(home),
        )
        self.assertEqual(rows[0].business_name, UNKNOWN)

    def test_store_page_address_sets_physical_store(self) -> None:
        home = extract_page_evidence(
            "<html><head><title>Rozina</title></head><body><p>Women's clothing boutique.</p></body></html>",
            source_url="https://rozina.example",
            final_url="https://rozina.example",
        )
        store = extract_page_evidence(
            STORE_HTML,
            source_url="https://rozina.example/stores",
            final_url="https://rozina.example/stores",
        )
        enriched = EnrichedEvidence(
            root_url="https://rozina.example",
            pages=[home, store],
            successful_urls=["https://rozina.example", "https://rozina.example/stores"],
            selected_urls=["https://rozina.example/stores"],
        )
        rows = identify_business_candidates(
            _candidate(),
            home,
            extract_signals(home),
            enriched=enriched,
        )
        self.assertEqual(rows[0].physical_store, PhysicalStore.YES)
        self.assertEqual(rows[0].city, "Mumbai")
        self.assertIn("Linking Road", rows[0].address or "")
        sources = rows[0].evidence.get("enrichment", {}).get("address_sources", [])
        self.assertTrue(any("/stores" in item.get("url", "") for item in sources))

    def test_multiple_cities_not_assigned(self) -> None:
        home = extract_page_evidence(
            "<html><head><title>Rozina</title></head><body><p>Women's clothing boutique.</p></body></html>",
            source_url="https://rozina.example",
            final_url="https://rozina.example",
        )
        stores = extract_page_evidence(
            "<html><title>Stores</title><body>"
            "<p>Showroom 12 Linking Road, Mumbai 400050</p>"
            "<p>Showroom 8 MG Road, Delhi 110024</p></body></html>",
            source_url="https://rozina.example/stores",
            final_url="https://rozina.example/stores",
        )
        enriched = EnrichedEvidence(
            root_url="https://rozina.example",
            pages=[home, stores],
            successful_urls=["https://rozina.example", "https://rozina.example/stores"],
            selected_urls=["https://rozina.example/stores"],
        )
        rows = identify_business_candidates(
            _candidate(),
            home,
            extract_signals(home),
            enriched=enriched,
        )
        self.assertEqual(rows[0].city, UNKNOWN)


URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://rozina.example/contact</loc><lastmod>2024-01-01</lastmod></url>
  <url><loc>https://rozina.example/stores</loc></url>
  <url><loc>https://rozina.example/about-us</loc></url>
  <url><loc>https://rozina.example/products/dress</loc></url>
  <url><loc>https://instagram.com/rozina</loc></url>
</urlset>
"""

INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://rozina.example/sitemap-pages.xml</loc></sitemap>
  <sitemap><loc>https://rozina.example/sitemap-a.xml</loc></sitemap>
  <sitemap><loc>https://rozina.example/sitemap-b.xml</loc></sitemap>
  <sitemap><loc>https://rozina.example/sitemap-c.xml</loc></sitemap>
  <sitemap><loc>https://rozina.example/sitemap-d.xml</loc></sitemap>
  <sitemap><loc>https://rozina.example/sitemap-e.xml</loc></sitemap>
  <sitemap><loc>https://rozina.example/sitemap-f.xml</loc></sitemap>
</sitemapindex>
"""

NESTED_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://rozina.example/sitemap-nested.xml</loc></sitemap>
</sitemapindex>
"""

CHILD_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://rozina.example/contact-us</loc></url>
</urlset>
"""

NESTED_CHILD = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://rozina.example/secret-from-nested-index</loc></url>
</urlset>
"""


class SitemapHelperTests(unittest.TestCase):
    def test_sitemap_xml_url_extraction(self) -> None:
        parsed = parse_sitemap_xml(URLSET)
        self.assertEqual(parsed.kind, "urlset")
        self.assertIn("https://rozina.example/contact", parsed.locs)
        self.assertIn("https://rozina.example/stores", parsed.locs)

    def test_robots_txt_sitemap_discovery(self) -> None:
        text = "User-agent: *\nAllow: /\nSitemap: https://rozina.example/sitemap.xml\n"
        self.assertEqual(
            parse_robots_sitemaps(text),
            ["https://rozina.example/sitemap.xml"],
        )
        fetcher = FakeFetcher(
            {
                "https://rozina.example/sitemap.xml": _ok(
                    "https://rozina.example/sitemap.xml",
                    URLSET,
                    content_type="application/xml",
                )
            },
            robots_sitemaps=["https://rozina.example/sitemap.xml"],
        )
        discovery = discover_sitemap_urls(fetcher, "https://rozina.example")
        self.assertTrue(discovery.discovered)
        self.assertEqual(discovery.source, "robots_sitemap")
        self.assertIn("https://rozina.example/contact", discovery.urls)
        self.assertNotIn("https://rozina.example/sitemap_index.xml", fetcher.fetched_urls)

    def test_sitemap_index_handling(self) -> None:
        responses = {
            "https://rozina.example/sitemap.xml": _ok(
                "https://rozina.example/sitemap.xml",
                INDEX_XML,
                content_type="application/xml",
            ),
            "https://rozina.example/sitemap-pages.xml": _ok(
                "https://rozina.example/sitemap-pages.xml",
                CHILD_URLSET,
                content_type="application/xml",
            ),
        }
        for name in "abcdef":
            responses[f"https://rozina.example/sitemap-{name}.xml"] = _ok(
                f"https://rozina.example/sitemap-{name}.xml",
                CHILD_URLSET,
                content_type="application/xml",
            )
        fetcher = FakeFetcher(responses)
        discovery = discover_sitemap_urls(fetcher, "https://rozina.example")
        self.assertEqual(discovery.source, "sitemap")
        self.assertEqual(discovery.child_sitemaps_fetched, 5)
        self.assertIn("https://rozina.example/contact-us", discovery.urls)
        self.assertNotIn("https://rozina.example/sitemap-f.xml", fetcher.fetched_urls)

    def test_malformed_xml_handling(self) -> None:
        parsed = parse_sitemap_xml("<urlset><loc>not-closed")
        self.assertEqual(parsed.kind, "invalid")
        self.assertEqual(parsed.locs, [])

    def test_missing_sitemap_handling(self) -> None:
        fetcher = FakeFetcher({})
        discovery = discover_sitemap_urls(fetcher, "https://rozina.example")
        self.assertFalse(discovery.discovered)
        self.assertEqual(discovery.urls, [])

    def test_product_urls_rejected(self) -> None:
        self.assertEqual(
            url_enrichment_score("https://rozina.example/products/dress", "https://rozina.example"),
            0,
        )
        self.assertEqual(
            url_enrichment_score("https://rozina.example/p/sku-1", "https://rozina.example"),
            0,
        )

    def test_contact_store_about_prioritized_from_sitemap(self) -> None:
        selected = select_enrichment_urls(
            [],
            "https://rozina.example",
            sitemap_urls=[
                "https://rozina.example/about-us",
                "https://rozina.example/stores",
                "https://rozina.example/contact-us",
                "https://rozina.example/products/x",
            ],
        )
        self.assertEqual(selected[0], "https://rozina.example/contact-us")
        self.assertEqual(selected[1], "https://rozina.example/stores")
        self.assertEqual(selected[2], "https://rozina.example/about-us")

    def test_external_sitemap_urls_rejected(self) -> None:
        fetcher = FakeFetcher(
            {
                "https://rozina.example/sitemap.xml": _ok(
                    "https://rozina.example/sitemap.xml",
                    URLSET,
                    content_type="application/xml",
                )
            }
        )
        discovery = discover_sitemap_urls(fetcher, "https://rozina.example")
        self.assertNotIn("https://instagram.com/rozina", discovery.urls)
        self.assertEqual(
            url_enrichment_score("https://instagram.com/rozina", "https://rozina.example"),
            0,
        )

    def test_duplicate_navigation_and_sitemap_deduped(self) -> None:
        chosen = select_enrichment_candidates(
            [_link("https://rozina.example/contact", "Contact")],
            "https://rozina.example",
            sitemap_urls=["https://rozina.example/contact/", "https://rozina.example/stores"],
        )
        urls = [item.url for item in chosen]
        self.assertEqual(urls.count("https://rozina.example/contact"), 1)
        contact = next(item for item in chosen if "contact" in item.url)
        self.assertEqual(contact.source, "homepage_link")

    def test_sitemap_does_not_increase_max_three_pages(self) -> None:
        html = """
        <html><title>Home</title><body>
        <a href="/contact">Contact</a>
        <a href="/stores">Stores</a>
        <a href="/about">About</a>
        </body></html>
        """
        fetcher = FakeFetcher(
            {
                "https://rozina.example": _ok("https://rozina.example", html),
                "https://rozina.example/contact": _ok(
                    "https://rozina.example/contact", CONTACT_HTML
                ),
                "https://rozina.example/stores": _ok(
                    "https://rozina.example/stores", STORE_HTML
                ),
                "https://rozina.example/about": _ok(
                    "https://rozina.example/about", ABOUT_HTML
                ),
                "https://rozina.example/visit-us": _ok(
                    "https://rozina.example/visit-us", STORE_HTML
                ),
                "https://rozina.example/sitemap.xml": _ok(
                    "https://rozina.example/sitemap.xml",
                    """<?xml version="1.0"?>
                    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                      <url><loc>https://rozina.example/shop</loc></url>
                      <url><loc>https://rozina.example/stockist</loc></url>
                      <url><loc>https://rozina.example/products/dress</loc></url>
                    </urlset>
                    """,
                    content_type="application/xml",
                ),
            }
        )
        result = enrich_candidate(fetcher, _candidate())
        self.assertEqual(len(result.selected_urls), 3)
        self.assertLessEqual(len(result.pages), 4)
        self.assertNotIn("https://rozina.example/shop", result.selected_urls)
        self.assertNotIn("https://rozina.example/stockist", result.selected_urls)
        self.assertNotIn("https://rozina.example/shop", fetcher.fetched_urls)
        self.assertNotIn("https://rozina.example/stockist", fetcher.fetched_urls)

    def test_navigation_preferred_when_equivalent(self) -> None:
        chosen = select_enrichment_candidates(
            [_link("https://rozina.example/about", "Our Story")],
            "https://rozina.example",
            sitemap_urls=["https://rozina.example/about-us"],
        )
        self.assertEqual(chosen[0].url, "https://rozina.example/about")
        self.assertEqual(chosen[0].source, "homepage_link")

    def test_sitemap_only_useful_pages_can_be_selected(self) -> None:
        html = "<html><title>Home</title><body><a href='/cart'>Cart</a></body></html>"
        fetcher = FakeFetcher(
            {
                "https://rozina.example": _ok("https://rozina.example", html),
                "https://rozina.example/stores": _ok(
                    "https://rozina.example/stores", STORE_HTML
                ),
                "https://rozina.example/sitemap.xml": _ok(
                    "https://rozina.example/sitemap.xml",
                    """<?xml version="1.0"?>
                    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                      <url><loc>https://rozina.example/stores</loc></url>
                      <url><loc>https://rozina.example/products/x</loc></url>
                    </urlset>
                    """,
                    content_type="application/xml",
                ),
            }
        )
        result = enrich_candidate(fetcher, _candidate())
        self.assertIn("https://rozina.example/stores", result.selected_urls)
        self.assertEqual(result.selected_url_sources[0]["source"], "sitemap")
        self.assertTrue(any(page.source_url.endswith("/stores") for page in result.pages))

    def test_no_recursive_sitemap_crawling_beyond_limit(self) -> None:
        fetcher = FakeFetcher(
            {
                "https://rozina.example/sitemap.xml": _ok(
                    "https://rozina.example/sitemap.xml",
                    NESTED_INDEX,
                    content_type="application/xml",
                ),
                "https://rozina.example/sitemap-nested.xml": _ok(
                    "https://rozina.example/sitemap-nested.xml",
                    INDEX_XML,
                    content_type="application/xml",
                ),
                "https://rozina.example/sitemap-pages.xml": _ok(
                    "https://rozina.example/sitemap-pages.xml",
                    NESTED_CHILD,
                    content_type="application/xml",
                ),
            }
        )
        discovery = discover_sitemap_urls(fetcher, "https://rozina.example")
        self.assertIn("https://rozina.example/sitemap-nested.xml", fetcher.fetched_urls)
        self.assertNotIn("https://rozina.example/sitemap-pages.xml", fetcher.fetched_urls)
        self.assertNotIn("https://rozina.example/secret-from-nested-index", discovery.urls)


def extract_signals(page):
    from content_extraction import extract_business_signals

    return extract_business_signals(page)


if __name__ == "__main__":
    unittest.main()
