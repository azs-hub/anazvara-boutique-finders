"""Unit tests for SearXNG JSON parsing (no network)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from searxng_provider import parse_searxng_json


SAMPLE_SEARXNG_JSON = {
    "query": "women's fashion boutique Mumbai",
    "number_of_results": 2,
    "results": [
        {
            "title": "Atelier East Bandra",
            "url": "https://atelier-east.example/mumbai",
            "pretty_url": "https://atelier-east.example/mumbai",
            "content": "Women's fashion boutique in Bandra, Mumbai.",
            "engine": "google",
        },
        {
            "title": "Linking Road Studio Store",
            "url": "https://linking-road-studio.example",
            "content": "Independent clothing boutique near Linking Road.",
            "engines": ["bing", "duckduckgo"],
        },
    ],
}


class ParseSearxngJsonTests(unittest.TestCase):
    def test_extracts_title_url_and_snippet_for_multiple_results(self) -> None:
        results = parse_searxng_json(SAMPLE_SEARXNG_JSON)

        self.assertEqual(len(results), 2)

        first = results[0]
        self.assertEqual(first.title, "Atelier East Bandra")
        self.assertEqual(first.url, "https://atelier-east.example/mumbai")
        self.assertEqual(first.snippet, "Women's fashion boutique in Bandra, Mumbai.")
        self.assertEqual(first.source, "google")

        second = results[1]
        self.assertEqual(second.title, "Linking Road Studio Store")
        self.assertEqual(second.url, "https://linking-road-studio.example")
        self.assertEqual(
            second.snippet,
            "Independent clothing boutique near Linking Road.",
        )
        self.assertEqual(second.source, "bing, duckduckgo")

    def test_empty_results_list(self) -> None:
        results = parse_searxng_json({"results": []})
        self.assertEqual(results, [])

    def test_missing_results_key(self) -> None:
        results = parse_searxng_json({"query": "mumbai boutique"})
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
