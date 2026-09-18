"""Offline tests for URL normalization, classification, and in-search dedup."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from candidates import Candidate, candidates_from_search_results, expand_from_page
from classification import ResultType, classify_url
from search_provider import SearchResult
from url_normalization import extract_domain, normalize_url


class UrlNormalizationTests(unittest.TestCase):
    def test_strips_fragment_www_and_trailing_slash(self) -> None:
        self.assertEqual(
            normalize_url("https://WWW.Example.com/about/#team"),
            "https://example.com/about",
        )

    def test_upgrades_http_and_drops_default_port(self) -> None:
        self.assertEqual(
            normalize_url("http://example.com:80/contact/"),
            "https://example.com/contact",
        )

    def test_preserves_business_path(self) -> None:
        self.assertEqual(
            normalize_url("https://www.example.com/stores/bandra"),
            "https://example.com/stores/bandra",
        )

    def test_homepage_has_no_trailing_slash(self) -> None:
        self.assertEqual(normalize_url("https://example.com/"), "https://example.com")

    def test_removes_tracking_parameters_and_keeps_others(self) -> None:
        url = (
            "https://example.com/shop?utm_source=google&utm_medium=cpc"
            "&utm_campaign=mumbai&id=42&fbclid=abc"
        )
        self.assertEqual(normalize_url(url), "https://example.com/shop?id=42")

    def test_same_domain_for_www_and_bare_host(self) -> None:
        self.assertEqual(extract_domain("https://www.example.com/about"), "example.com")
        self.assertEqual(extract_domain("https://example.com/contact"), "example.com")
        self.assertEqual(extract_domain("http://example.com/"), "example.com")

    def test_keeps_non_www_subdomain(self) -> None:
        self.assertEqual(extract_domain("https://shop.example.com/"), "shop.example.com")

    def test_empty_url(self) -> None:
        self.assertIsNone(normalize_url(""))
        self.assertIsNone(extract_domain(""))


class ClassificationTests(unittest.TestCase):
    def test_instagram_is_social(self) -> None:
        self.assertEqual(
            classify_url("https://www.instagram.com/someboutique/"),
            ResultType.SOCIAL,
        )

    def test_youtube_is_video(self) -> None:
        self.assertEqual(
            classify_url("https://www.youtube.com/watch?v=abc123"),
            ResultType.VIDEO,
        )

    def test_directory_hosts(self) -> None:
        self.assertEqual(
            classify_url("https://www.justdial.com/Mumbai/Boutiques"),
            ResultType.DIRECTORY,
        )
        self.assertEqual(
            classify_url("https://lbb.in/mumbai/best-boutiques/"),
            ResultType.DIRECTORY,
        )
        self.assertEqual(
            classify_url("https://wanderlog.com/list/geoCategory/123/mumbai"),
            ResultType.DIRECTORY,
        )

    def test_article_host(self) -> None:
        self.assertEqual(
            classify_url("https://www.vogue.in/fashion/article/mumbai-boutiques"),
            ResultType.ARTICLE,
        )

    def test_normal_domain_is_website(self) -> None:
        self.assertEqual(
            classify_url("https://www.sample-mumbai-boutique.example/about"),
            ResultType.WEBSITE,
        )

    def test_unknown_without_host(self) -> None:
        self.assertEqual(classify_url(""), ResultType.UNKNOWN)
        self.assertEqual(classify_url("mailto:hello@example.com"), ResultType.UNKNOWN)


class DuplicateWebsiteTests(unittest.TestCase):
    def test_website_variants_collapse_to_homepage(self) -> None:
        results = [
            SearchResult(
                title="About",
                url="https://www.example.com/about",
                snippet="About the boutique",
                source="google",
            ),
            SearchResult(
                title="Home",
                url="https://example.com/",
                snippet="Homepage",
                source="bing",
            ),
            SearchResult(
                title="Contact",
                url="http://example.com/contact",
                snippet="Contact us",
                source="google",
            ),
        ]
        candidates = candidates_from_search_results(results, "boutique mumbai")
        websites = [row for row in candidates if row.result_type is ResultType.WEBSITE]
        self.assertEqual(len(websites), 1)
        self.assertEqual(websites[0].domain, "example.com")
        self.assertEqual(websites[0].normalized_url, "https://example.com")

    def test_directory_pages_are_kept_separately(self) -> None:
        results = [
            SearchResult(
                title="LBB list",
                url="https://lbb.in/mumbai/boutiques-one",
                snippet="List one",
                source="google",
            ),
            SearchResult(
                title="LBB list 2",
                url="https://lbb.in/mumbai/boutiques-two",
                snippet="List two",
                source="google",
            ),
            SearchResult(
                title="Shop",
                url="https://actual-boutique.example/",
                snippet="A shop",
                source="google",
            ),
        ]
        candidates = candidates_from_search_results(results, "boutique mumbai")
        self.assertEqual(len(candidates), 3)
        types = [row.result_type for row in candidates]
        self.assertEqual(types.count(ResultType.DIRECTORY), 2)
        self.assertEqual(types.count(ResultType.WEBSITE), 1)

    def test_expand_from_page_is_reserved(self) -> None:
        candidate = Candidate(
            title="LBB",
            url="https://lbb.in/mumbai/boutiques",
            normalized_url="https://lbb.in/mumbai/boutiques",
            domain="lbb.in",
            snippet="",
            result_type=ResultType.DIRECTORY,
            search_query="test",
            search_source="google",
        )
        with self.assertRaises(NotImplementedError):
            expand_from_page(candidate)


if __name__ == "__main__":
    unittest.main()
