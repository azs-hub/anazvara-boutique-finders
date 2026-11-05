"""Offline tests for enrichment-impact measurement helpers."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from enrichment_benchmark import (
    baseline_metrics,
    compare_snapshots,
    comparison_table,
    field_populated,
    find_baseline,
    http_kind,
    parse_limits,
    quality_score,
    suspicious_reasons,
)


class ParseLimitsTests(unittest.TestCase):
    def test_default_is_50_and_100(self) -> None:
        self.assertEqual(parse_limits(None, None), [50, 100])

    def test_single_limit(self) -> None:
        self.assertEqual(parse_limits(50, None), [50])

    def test_comma_limits(self) -> None:
        self.assertEqual(parse_limits(None, "50,100"), [50, 100])

    def test_rejects_zero(self) -> None:
        with self.assertRaises(ValueError):
            parse_limits(0, None)


class FieldAndGainTests(unittest.TestCase):
    def test_unknown_is_empty(self) -> None:
        self.assertFalse(field_populated("UNKNOWN"))
        self.assertFalse(field_populated(None))
        self.assertFalse(field_populated(""))
        self.assertTrue(field_populated("Rozina"))

    def test_gained_name_and_phone(self) -> None:
        before = {
            "business_name": "UNKNOWN",
            "address": None,
            "city": "UNKNOWN",
            "phone": None,
            "email": None,
            "instagram": None,
            "physical_store": "UNKNOWN",
            "business_type": "UNKNOWN",
            "women_fashion_relevance": "UNKNOWN",
            "confidence": "LOW",
        }
        after = {
            **before,
            "business_name": "Rozina",
            "phone": "+91 22 1234 5678",
            "city": "Mumbai",
            "business_type": "BOUTIQUE",
            "physical_store": "YES",
            "women_fashion_relevance": "HIGH",
            "confidence": "HIGH",
        }
        result = compare_snapshots(before, after)
        self.assertIn("business_name", result["gained"])
        self.assertIn("phone", result["gained"])
        self.assertIn("city", result["gained"])
        self.assertIn("physical_store", result["gained"])
        self.assertIn("business_type", result["gained"])
        self.assertIn("women_fashion_relevance", result["gained"])
        self.assertIn("high_confidence", result["gained"])
        self.assertEqual(result["lost"], [])

    def test_lost_and_worse_phone(self) -> None:
        before = {
            "business_name": "Rozina",
            "address": "1 Linking Road",
            "city": "Mumbai",
            "phone": "9999999999",
            "email": "a@example.com",
            "instagram": "https://instagram.com/rozina",
            "physical_store": "YES",
            "business_type": "BOUTIQUE",
            "women_fashion_relevance": "HIGH",
            "confidence": "HIGH",
        }
        after = {
            **before,
            "phone": None,
            "physical_store": "UNKNOWN",
            "confidence": "LOW",
        }
        result = compare_snapshots(before, after)
        self.assertIn("phone", result["lost"])
        self.assertIn("physical_store", result["lost"])
        self.assertIn("high_confidence", result["lost"])
        self.assertIn("phone", result["worse"])
        self.assertIn("confidence", result["worse"])

    def test_changed_city_is_not_automatically_gain(self) -> None:
        before = {
            "business_name": "Rozina",
            "address": None,
            "city": "Mumbai",
            "phone": None,
            "email": None,
            "instagram": None,
            "physical_store": "UNKNOWN",
            "business_type": "BOUTIQUE",
            "women_fashion_relevance": "MEDIUM",
            "confidence": "MEDIUM",
        }
        after = {**before, "city": "Delhi"}
        result = compare_snapshots(before, after)
        self.assertIn("city", result["changed"])
        self.assertNotIn("city", result["gained"])
        self.assertNotIn("city", result["lost"])

    def test_chrome_name_change_is_worse(self) -> None:
        before = {
            "business_name": "Rozina",
            "address": None,
            "city": "UNKNOWN",
            "phone": None,
            "email": None,
            "instagram": None,
            "physical_store": "UNKNOWN",
            "business_type": "UNKNOWN",
            "women_fashion_relevance": "UNKNOWN",
            "confidence": "LOW",
        }
        after = {**before, "business_name": "Shopping Cart"}
        result = compare_snapshots(before, after)
        self.assertIn("business_name", result["changed"])
        self.assertIn("business_name", result["worse"])


class SuspiciousTests(unittest.TestCase):
    def test_ecommerce_ui_name(self) -> None:
        reasons = suspicious_reasons(
            {
                "business_name": "Item added to cart",
                "website": "https://shop.example/",
                "city": "UNKNOWN",
                "source_type": "WEBSITE",
                "business_type": "UNKNOWN",
                "physical_store": "UNKNOWN",
                "confidence": "LOW",
            },
            query="women's fashion boutique Mumbai",
        )
        self.assertIn("ecommerce_ui_name", reasons)

    def test_unrelated_city(self) -> None:
        reasons = suspicious_reasons(
            {
                "business_name": "Some Store",
                "website": "https://store.example/",
                "city": "Delhi",
                "source_type": "WEBSITE",
                "business_type": "BOUTIQUE",
                "physical_store": "YES",
                "confidence": "MEDIUM",
            },
            query="women's fashion boutique Mumbai",
        )
        self.assertIn("unrelated_city", reasons)

    def test_directory_and_marketplace(self) -> None:
        reasons = suspicious_reasons(
            {
                "business_name": "Justdial listings",
                "website": "https://justdial.com/mumbai/boutiques",
                "city": "Mumbai",
                "source_type": "DIRECTORY",
                "business_type": "MARKETPLACE",
                "physical_store": "UNKNOWN",
                "confidence": "LOW",
            },
            query="women's fashion boutique Mumbai",
        )
        self.assertIn("directory", reasons)
        self.assertIn("marketplace", reasons)

    def test_brand_without_store(self) -> None:
        reasons = suspicious_reasons(
            {
                "business_name": "Label X",
                "website": "https://labelx.example/",
                "city": "UNKNOWN",
                "source_type": "WEBSITE",
                "business_type": "BRAND",
                "physical_store": "UNKNOWN",
                "address": None,
                "confidence": "MEDIUM",
            },
            query="women's fashion boutique Mumbai",
        )
        self.assertIn("brand_without_store_evidence", reasons)

    def test_mumbai_neighborhood_is_not_unrelated(self) -> None:
        reasons = suspicious_reasons(
            {
                "business_name": "Bandra Shop",
                "website": "https://bandra.example/",
                "city": "Bandra",
                "source_type": "WEBSITE",
                "business_type": "BOUTIQUE",
                "physical_store": "YES",
                "confidence": "HIGH",
            },
            query="women's fashion boutique Mumbai",
        )
        self.assertNotIn("unrelated_city", reasons)


class ComparisonTableTests(unittest.TestCase):
    def test_signed_change(self) -> None:
        before = {
            "candidates": 50,
            "raw_business_candidates": 38,
            "deduplicated_candidates": 33,
            "high_confidence": 10,
            "physical_store_yes": 4,
            "city_populated": 2,
            "address_populated": 4,
            "phone_populated": 13,
            "email_populated": 10,
            "instagram_populated": 21,
            "high_women_fashion": 11,
            "total_fetches": 50,
            "failed_fetches": 22,
            "runtime_seconds": None,
        }
        after = {
            **before,
            "high_confidence": 14,
            "total_fetches": 180,
            "runtime_seconds": 200.0,
        }
        rows = {row["metric"]: row for row in comparison_table(before, after)}
        self.assertEqual(rows["high_confidence"]["change"], 4)
        self.assertEqual(rows["total_fetches"]["change"], 130)
        self.assertIsNone(rows["runtime_seconds"]["change"])

    def test_baseline_metrics_from_step_65_shape(self) -> None:
        payload = {
            "limit": 50,
            "counts": {
                "discovery": {"candidates": 50},
                "fetching": {"fetched_successfully": 28, "blocked_failed": 22},
                "identification": {
                    "raw": 38,
                    "after_dedup": 33,
                    "confidence": {"HIGH": 10},
                    "physical_store": {"YES": 4},
                    "women_fashion": {"HIGH": 11},
                    "contact": {
                        "City": 2,
                        "Address": 4,
                        "Phone": 13,
                        "Email": 10,
                        "Instagram": 21,
                    },
                },
            },
        }
        metrics = baseline_metrics(payload)
        self.assertEqual(metrics["candidates"], 50)
        self.assertEqual(metrics["total_fetches"], 50)
        self.assertEqual(metrics["failed_fetches"], 22)
        self.assertEqual(metrics["high_confidence"], 10)


class BaselineLookupTests(unittest.TestCase):
    def test_skips_enriched_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            enriched = folder / "benchmark_enriched_20260101_000000.json"
            enriched.write_text(json.dumps({"limit": 50, "query": "q"}), encoding="utf-8")
            baseline = folder / "benchmark_20260101_000001.json"
            baseline.write_text(
                json.dumps(
                    {
                        "limit": 50,
                        "query": "women's fashion boutique Mumbai",
                        "counts": {},
                    }
                ),
                encoding="utf-8",
            )
            import enrichment_benchmark as module

            original = module.OUTPUT_DIR
            module.OUTPUT_DIR = folder
            try:
                found = find_baseline(50, "women's fashion boutique Mumbai")
            finally:
                module.OUTPUT_DIR = original
            self.assertEqual(found, baseline)

    def test_quality_score_prefers_gains(self) -> None:
        high_gain = {
            "comparison": {"gained": ["city", "phone"]},
            "after": {
                "confidence": "HIGH",
                "physical_store": "YES",
                "source_type": "WEBSITE",
            },
        }
        no_gain = {
            "comparison": {"gained": []},
            "after": {
                "confidence": "HIGH",
                "physical_store": "YES",
                "source_type": "WEBSITE",
            },
        }
        self.assertGreater(quality_score(high_gain), quality_score(no_gain))

    def test_http_kind(self) -> None:
        self.assertEqual(http_kind("https://example.com/robots.txt"), "robots")
        self.assertEqual(http_kind("https://example.com/sitemap.xml"), "sitemap")
        self.assertEqual(http_kind("https://example.com/"), "html")


if __name__ == "__main__":
    unittest.main()
