"""Tests for name ranking, structured facts, adaptive enrichment, and merge."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from business_candidates import (
    UNKNOWN,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
    identify_business_candidates,
    merge_in_memory_duplicates,
)
from candidates import Candidate
from classification import ResultType
from content_extraction import (
    BusinessSignals,
    extract_business_signals,
    extract_page_evidence,
)
from enrichment import EnrichedEvidence, enrich_candidate
from fetcher import FetchResult
from structured_evidence import (
    EnrichmentTier,
    assess_enrichment_need,
    choose_business_name,
    collect_name_candidates,
)


def _candidate(**kwargs) -> Candidate:
    defaults = dict(
        title="Rozina",
        url="https://rozina.example/",
        normalized_url="https://rozina.example",
        domain="rozina.example",
        snippet="",
        result_type=ResultType.WEBSITE,
        search_query="women's fashion boutique Mumbai",
        search_source="test",
    )
    defaults.update(kwargs)
    return Candidate(**defaults)


def _ok(url: str, html: str) -> FetchResult:
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        html=html,
        error=None,
        fetched=True,
        elapsed_seconds=0.01,
    )


class FakeFetcher:
    def __init__(self, responses: dict[str, FetchResult]) -> None:
        self.responses = responses
        self.fetched_urls: list[str] = []

    def fetch(self, target, *, allow_xml: bool = False) -> FetchResult:
        from url_normalization import normalize_url

        url = target if isinstance(target, str) else (target.normalized_url or target.url)
        key = (normalize_url(url) or url).rstrip("/")
        self.fetched_urls.append(key)
        if key in self.responses:
            return self.responses[key]
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=404,
            content_type="text/html",
            html=None,
            error="missing fake",
            fetched=False,
            elapsed_seconds=0.01,
        )

    def robots_sitemap_urls(self, _page_url: str) -> list[str]:
        return []


JSONLD_HOME = """
<html><head>
  <title>Order Summary</title>
  <meta property="og:site_name" content="Rozina">
  <script type="application/ld+json">
  {
    "@type": "LocalBusiness",
    "name": "Rozina",
    "telephone": "+91 98765 43210",
    "email": "hello@rozina.example",
    "sameAs": "https://instagram.com/rozina",
    "address": {
      "@type": "PostalAddress",
      "streetAddress": "12 Linking Road",
      "addressLocality": "Mumbai",
      "postalCode": "400050"
    }
  }
  </script>
</head>
<body>
  <h1>Order Summary</h1>
  <p>Women's clothing boutique. Visit us at our store.</p>
  <a href="/contact">Contact</a>
  <a href="/stores">Stores</a>
  <a href="/about">About</a>
</body></html>
"""


class NamePriorityTests(unittest.TestCase):
    def test_jsonld_organization_beats_page_title(self) -> None:
        html = """
        <html><head>
          <title>Order Summary</title>
          <script type="application/ld+json">
          {"@type":"Organization","name":"Rozina"}
          </script>
        </head><body><h1>Cart</h1></body></html>
        """
        page = extract_page_evidence(
            html, source_url="https://rozina.example", final_url="https://rozina.example"
        )
        row = identify_business_candidates(_candidate(title="Order Summary"), page, extract_business_signals(page))[0]
        self.assertEqual(row.business_name, "Rozina")
        self.assertEqual(row.evidence["name_source"], "jsonld_organization")
        self.assertEqual(row.evidence["name_confidence"], "HIGH")

    def test_og_site_name_beats_search_title(self) -> None:
        html = """
        <html><head>
          <title>New Arrivals</title>
          <meta property="og:site_name" content="Rozina">
        </head><body><p>Women's clothing boutique.</p></body></html>
        """
        page = extract_page_evidence(
            html, source_url="https://rozina.example", final_url="https://rozina.example"
        )
        row = identify_business_candidates(
            _candidate(title="Rozina official website"),
            page,
            extract_business_signals(page),
        )[0]
        self.assertEqual(row.business_name, "Rozina")
        self.assertEqual(row.evidence["name_source"], "og_site_name")

    def test_generic_and_transactional_names_rejected(self) -> None:
        page = extract_page_evidence(
            "<html><head><title>Checkout</title></head><body><h1>Cart</h1></body></html>",
            source_url="https://rozina.example",
            final_url="https://rozina.example",
        )
        names = collect_name_candidates(
            page=page, search_title="Checkout", page_role="homepage"
        )
        name, source, _confidence = choose_business_name(names)
        self.assertEqual(name, UNKNOWN)
        self.assertEqual(source, "none")

    def test_search_title_is_last_resort(self) -> None:
        page = extract_page_evidence(
            "<html><head><title>Home</title></head><body><p>Welcome.</p></body></html>",
            source_url="https://rozina.example",
            final_url="https://rozina.example",
        )
        row = identify_business_candidates(
            _candidate(title="Rozina"),
            page,
            extract_business_signals(page),
        )[0]
        self.assertEqual(row.business_name, "Rozina")
        self.assertEqual(row.evidence["name_source"], "search_title")
        self.assertEqual(row.evidence["name_confidence"], "LOW")


class ClassificationCombinationTests(unittest.TestCase):
    def test_boutique_from_independent_signals(self) -> None:
        html = """
        <html><head>
          <script type="application/ld+json">
          {"@type":"LocalBusiness","name":"Rozina",
           "address":{"@type":"PostalAddress","streetAddress":"12 Linking Road",
           "addressLocality":"Mumbai","postalCode":"400050"}}
          </script>
        </head>
        <body><p>Women's fashion boutique. Visit us. Ethnic wear and dresses.</p></body></html>
        """
        page = extract_page_evidence(
            html, source_url="https://rozina.example", final_url="https://rozina.example"
        )
        row = identify_business_candidates(_candidate(), page, extract_business_signals(page))[0]
        self.assertEqual(row.business_type, BusinessType.BOUTIQUE)
        self.assertEqual(row.women_fashion_relevance, Relevance.HIGH)
        self.assertEqual(row.physical_store, PhysicalStore.YES)
        self.assertEqual(row.city, "Mumbai")
        physical = next(
            item for item in row.evidence["field_evidence"] if item["field"] == "physical_store"
        )
        self.assertEqual(physical["source"], "jsonld_localbusiness")
        self.assertEqual(physical["confidence"], "HIGH")

    def test_does_not_infer_women_from_fashion_type_alone(self) -> None:
        page = extract_page_evidence(
            "<html><title>Studio X</title><body><p>Fashion designer studio and atelier.</p></body></html>",
            source_url="https://studio.example",
            final_url="https://studio.example",
        )
        row = identify_business_candidates(
            _candidate(title="Studio X"), page, extract_business_signals(page)
        )[0]
        self.assertEqual(row.women_fashion_relevance, Relevance.UNKNOWN)

    def test_online_only_is_not_physical_store(self) -> None:
        page = extract_page_evidence(
            "<html><title>Rozina</title><body><p>Women's clothing boutique. Online-only store.</p></body></html>",
            source_url="https://rozina.example",
            final_url="https://rozina.example",
        )
        row = identify_business_candidates(_candidate(), page, extract_business_signals(page))[0]
        self.assertEqual(row.physical_store, PhysicalStore.NO)

    def test_designer_combination(self) -> None:
        page = extract_page_evidence(
            "<html><title>Ana Designer</title><body>"
            "<p>Fashion designer house. Couture atelier and designer collection.</p>"
            "</body></html>",
            source_url="https://ana.example",
            final_url="https://ana.example",
        )
        row = identify_business_candidates(
            _candidate(title="Ana Designer"), page, extract_business_signals(page)
        )[0]
        self.assertEqual(row.business_type, BusinessType.DESIGNER)


class EvidenceMergeTests(unittest.TestCase):
    def test_jsonld_name_not_replaced_by_contact_title(self) -> None:
        home = extract_page_evidence(
            JSONLD_HOME,
            source_url="https://rozina.example",
            final_url="https://rozina.example",
        )
        contact = extract_page_evidence(
            "<html><title>Contact Us</title><h1>Get in Touch</h1></html>",
            source_url="https://rozina.example/contact",
            final_url="https://rozina.example/contact",
        )
        row = identify_business_candidates(
            _candidate(),
            home,
            extract_business_signals(home),
            enriched=EnrichedEvidence(
                root_url="https://rozina.example",
                pages=[home, contact],
            ),
        )[0]
        self.assertEqual(row.business_name, "Rozina")
        self.assertIn(row.evidence["name_source"], {"jsonld_localbusiness", "og_site_name"})
        self.assertEqual(row.evidence["name_confidence"], "HIGH")

    def test_duplicate_merge_keeps_stronger_name(self) -> None:
        strong = identify_business_candidates(
            _candidate(),
            extract_page_evidence(
                JSONLD_HOME,
                source_url="https://rozina.example",
                final_url="https://rozina.example",
            ),
            BusinessSignals(),
        )[0]
        weak = identify_business_candidates(
            _candidate(title="Shopping Cart"),
            extract_page_evidence(
                "<html><title>Shopping Cart</title></html>",
                source_url="https://rozina.example/cart",
                final_url="https://rozina.example/cart",
            ),
            BusinessSignals(),
        )[0]
        weak.website = strong.website
        merged = merge_in_memory_duplicates([weak, strong])[0]
        self.assertEqual(merged.business_name, "Rozina")


class AdaptiveEnrichmentTests(unittest.TestCase):
    def test_strong_homepage_skips_sitemap_and_extra_pages(self) -> None:
        fetcher = FakeFetcher(
            {
                "https://rozina.example": _ok("https://rozina.example", JSONLD_HOME),
                "https://rozina.example/contact": _ok(
                    "https://rozina.example/contact", "<html><title>Contact</title></html>"
                ),
                "https://rozina.example/sitemap.xml": _ok(
                    "https://rozina.example/sitemap.xml",
                    '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    "<url><loc>https://rozina.example/contact</loc></url></urlset>",
                ),
            }
        )
        result = enrich_candidate(fetcher, _candidate())
        self.assertEqual(result.enrichment_tier, EnrichmentTier.STRONG.value)
        self.assertTrue(result.sitemap_skipped)
        self.assertEqual(result.selected_urls, [])
        self.assertFalse(any("sitemap" in url for url in fetcher.fetched_urls))
        self.assertFalse(any(url.endswith("/contact") for url in fetcher.fetched_urls))

    def test_partial_enrichment_fetches_only_missing_location(self) -> None:
        html = """
        <html><head><title>Rozina</title>
        <meta property="og:site_name" content="Rozina">
        </head>
        <body>
          <p>Women's clothing boutique. Email hello@rozina.example</p>
          <a href="/contact">Contact</a>
          <a href="/stores">Our Stores</a>
          <a href="/about">About</a>
        </body></html>
        """
        page = extract_page_evidence(
            html, source_url="https://rozina.example", final_url="https://rozina.example"
        )
        decision = assess_enrichment_need(page, extract_business_signals(page))
        self.assertEqual(decision.tier, EnrichmentTier.PARTIAL)
        self.assertEqual(decision.wanted_roles, ("location",))

        fetcher = FakeFetcher(
            {
                "https://rozina.example": _ok("https://rozina.example", html),
                "https://rozina.example/contact": _ok(
                    "https://rozina.example/contact", "<html><title>Contact</title></html>"
                ),
                "https://rozina.example/stores": _ok(
                    "https://rozina.example/stores",
                    "<html><title>Stores</title><body>"
                    "<p>Visit us at 12 Linking Road, Bandra West, Mumbai 400050.</p>"
                    "</body></html>",
                ),
                "https://rozina.example/about": _ok(
                    "https://rozina.example/about", "<html><title>About Rozina</title></html>"
                ),
            }
        )
        result = enrich_candidate(fetcher, _candidate())
        self.assertTrue(result.sitemap_skipped)
        self.assertTrue(any(url.endswith("/stores") for url in result.selected_urls))
        self.assertFalse(any(url.endswith("/contact") for url in result.selected_urls))
        self.assertFalse(any(url.endswith("/about") for url in result.selected_urls))


class SitemapCacheTests(unittest.TestCase):
    def test_sitemap_discovery_is_cached_on_fetcher(self) -> None:
        from sitemap import discover_sitemap_urls

        xml = (
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://rozina.example/contact</loc></url></urlset>"
        )
        fetcher = FakeFetcher(
            {
                "https://rozina.example/sitemap.xml": _ok(
                    "https://rozina.example/sitemap.xml", xml
                )
            }
        )
        first = discover_sitemap_urls(fetcher, "https://rozina.example")
        second = discover_sitemap_urls(fetcher, "https://rozina.example/about")
        self.assertEqual(first.urls, second.urls)
        sitemap_fetches = [url for url in fetcher.fetched_urls if "sitemap" in url]
        self.assertEqual(len(sitemap_fetches), 1)


if __name__ == "__main__":
    unittest.main()
