"""Offline tests for stockist benchmark helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from business_candidates import (
    UNKNOWN,
    BusinessCandidate,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
)
from candidates import Candidate
from classification import ResultType
from goa_stockist_benchmark import (
    looks_like_villa_mor,
    merge_candidates,
    reporting_bucket,
)
from llm_benchmark import (
    compare_stockist,
    match_stockist_references,
    stockist_counts,
)


def _row(**kwargs) -> BusinessCandidate:
    defaults = dict(
        business_name="Example",
        website="https://example.com",
        instagram=None,
        facebook=None,
        whatsapp=None,
        phone=None,
        email=None,
        address=None,
        city=UNKNOWN,
        source_url="https://example.com",
        source_type="WEBSITE",
        business_type=BusinessType.UNKNOWN,
        fashion_relevance=Relevance.UNKNOWN,
        women_fashion_relevance=Relevance.UNKNOWN,
        physical_store=PhysicalStore.UNKNOWN,
        evidence={},
        confidence=Confidence.LOW,
    )
    defaults.update(kwargs)
    return BusinessCandidate(**defaults)


class StockistBenchmarkHelperTests(unittest.TestCase):
    def test_before_ai_is_unknown_after_ai_counts_yes(self) -> None:
        rows = [
            _row(
                evidence={
                    "rule_potential_stockist": "UNKNOWN",
                    "ai_potential_stockist": "YES",
                }
            ),
            _row(
                evidence={
                    "rule_potential_stockist": "UNKNOWN",
                    "ai_potential_stockist": "NO",
                }
            ),
        ]
        self.assertEqual(
            stockist_counts(rows, after_ai=False),
            {"YES": 0, "NO": 0, "UNKNOWN": 2},
        )
        self.assertEqual(
            stockist_counts(rows, after_ai=True),
            {"YES": 1, "NO": 1, "UNKNOWN": 0},
        )

    def test_unknown_to_yes_is_improved(self) -> None:
        cmp = compare_stockist("UNKNOWN", "YES", _row())
        self.assertTrue(cmp["changed"])
        self.assertTrue(cmp["improved"])
        self.assertFalse(cmp["weakened"])

    def test_reference_examples_are_benchmark_only(self) -> None:
        rows = [
            _row(
                business_name="Villa Mor",
                website="https://villamor.example",
                evidence={"ai_potential_stockist": "YES"},
            ),
            _row(
                business_name="Rozina",
                website="https://rozina.example",
                evidence={"ai_potential_stockist": "NO"},
            ),
        ]
        results = {item["label"]: item for item in match_stockist_references(rows)}
        self.assertTrue(results["Villa Mor"]["passed"])
        self.assertTrue(results["Rozina"]["passed"])
        self.assertIn("Yellow House Parra", results)
        self.assertIn("Rangeela Goa", results)
        self.assertIn("Paper Boat Collective", results)


class GoaBenchmarkHelperTests(unittest.TestCase):
    def test_merge_keeps_one_website_per_domain(self) -> None:
        first = Candidate(
            title="A",
            url="https://shop.example/about",
            normalized_url="https://shop.example/about",
            domain="shop.example",
            snippet="",
            result_type=ResultType.WEBSITE,
            search_query="fashion boutiques Goa",
            search_source="test",
        )
        second = Candidate(
            title="B",
            url="https://shop.example/",
            normalized_url="https://shop.example",
            domain="shop.example",
            snippet="",
            result_type=ResultType.WEBSITE,
            search_query="fashion boutiques Anjuna",
            search_source="test",
        )
        merged = merge_candidates([first], [second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].normalized_url, "https://shop.example")

    def test_reporting_buckets(self) -> None:
        self.assertEqual(
            reporting_bucket(_row(source_type="DIRECTORY")),
            "D_directory",
        )
        self.assertEqual(
            reporting_bucket(
                _row(
                    source_type="ARTICLE",
                    evidence={"signals": ["roundup_page"], "ai_potential_stockist": "NO"},
                )
            ),
            "E_article_or_editorial",
        )
        self.assertEqual(
            reporting_bucket(
                _row(
                    business_type=BusinessType.MULTI_DESIGNER,
                    evidence={"ai_potential_stockist": "YES"},
                )
            ),
            "A_multi_brand_or_curated_stockist",
        )
        self.assertEqual(
            reporting_bucket(_row(business_type=BusinessType.DESIGNER)),
            "B_own_label_designer_or_brand",
        )

    def test_villa_mor_match_is_title_or_url_only(self) -> None:
        hit = Candidate(
            title="Villa Mor (@villamor_shop)",
            url="https://www.instagram.com/villamor_shop/",
            normalized_url="https://instagram.com/villamor_shop",
            domain="instagram.com",
            snippet="",
            result_type=ResultType.SOCIAL,
            search_query="Villa Mor boutique Goa",
            search_source="test",
        )
        miss = Candidate(
            title="Random boutique",
            url="https://example.com",
            normalized_url="https://example.com",
            domain="example.com",
            snippet="",
            result_type=ResultType.WEBSITE,
            search_query="fashion boutiques Goa",
            search_source="test",
        )
        self.assertTrue(looks_like_villa_mor(hit))
        self.assertFalse(looks_like_villa_mor(miss))


if __name__ == "__main__":
    unittest.main()
