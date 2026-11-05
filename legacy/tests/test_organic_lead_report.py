"""Offline tests for the organic discovery funnel report."""

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
from organic_lead_report import (
    build_organic_report,
    compare_seed_benchmark,
    failure_class,
    unknown_missing_evidence,
)


def _row(**kwargs) -> BusinessCandidate:
    defaults = dict(
        business_name="Example Boutique",
        website="https://example.com",
        instagram=None,
        facebook=None,
        whatsapp=None,
        phone=None,
        email=None,
        address=None,
        city="Goa",
        source_url="https://example.com",
        source_type="WEBSITE",
        business_type=BusinessType.UNKNOWN,
        fashion_relevance=Relevance.UNKNOWN,
        women_fashion_relevance=Relevance.UNKNOWN,
        physical_store=PhysicalStore.UNKNOWN,
        evidence={"signals": ["direct_website"], "stockist_lead": "UNKNOWN"},
        confidence=Confidence.MEDIUM,
    )
    defaults.update(kwargs)
    return BusinessCandidate(**defaults)


class OrganicLeadReportTests(unittest.TestCase):
    def test_funnel_counts_yes_lead_and_listing_separately(self) -> None:
        yes = _row(
            email="hello@example.com",
            instagram="https://instagram.com/example",
            evidence={
                "signals": ["direct_website"],
                "ai_potential_stockist": "NO",
                "stockist_lead": "YES",
                "manual_review": True,
                "stockist_lead_reasons": [
                    "fashion_retail_page_evidence",
                    "official_website",
                    "contactable",
                    "address_not_required",
                ],
            },
        )
        listing = _row(
            business_name="Best Boutiques in Goa",
            website=None,
            source_type="ARTICLE",
            source_url="https://magazine.example/goa",
            evidence={"signals": ["roundup_page"], "stockist_lead": "NO"},
        )
        unnamed = _row(
            business_name=UNKNOWN,
            website=None,
            source_url="https://unknown.example",
            evidence={"signals": ["direct_website"], "stockist_lead": "UNKNOWN"},
        )
        report = build_organic_report(
            raw_search_count=5,
            organic_candidates=3,
            rows=[yes, listing, unnamed],
            pair_records=[
                {"rules": {"business_name": "Example Boutique", "source_url": "https://example.com"}},
                {"rules": {"business_name": "Best Boutiques in Goa", "source_url": "x"}},
                {"rules": {"business_name": None, "source_url": None}},
            ],
        )
        self.assertEqual(report["organic_candidates"], 3)
        self.assertEqual(report["unique_candidates_after_dedup"], 3)
        self.assertEqual(report["valid_business_entities"], 1)
        self.assertEqual(report["invalid_article_directory_listing"], 1)
        self.assertEqual(report["identity_found"], 1)
        self.assertEqual(report["identity_missing"], 1)
        self.assertEqual(report["not_evaluated"], 1)
        self.assertEqual(report["stockist_lead"]["YES"], 1)
        self.assertEqual(report["leads_with_website"], 1)
        self.assertEqual(report["leads_with_instagram"], 1)
        self.assertEqual(report["leads_with_email"], 1)
        self.assertEqual(report["leads_without_address"], 1)
        self.assertEqual(report["leads_with_multiple_contacts"], 1)
        self.assertEqual(report["leads_with_verified_official_website"], 1)
        self.assertEqual(report["manual_review_true"], 1)
        self.assertEqual(report["yes_leads"][0]["business_name"], "Example Boutique")
        self.assertEqual(report["yes_leads"][0]["potential_stockist"], "NO")

    def test_failure_classes_are_distinct(self) -> None:
        insufficient = _row(
            evidence={
                "signals": ["direct_website"],
                "stockist_lead": "UNKNOWN",
                "stockist_lead_reasons": ["fashion_retail_evidence_missing"],
            }
        )
        missing_id = _row(
            business_name=UNKNOWN,
            website=None,
            evidence={"signals": ["direct_website"], "stockist_lead": "UNKNOWN"},
        )
        mistaken = _row(
            business_name="Justdial Boutiques",
            source_type="DIRECTORY",
            business_type=BusinessType.BOUTIQUE,
            evidence={"signals": [], "stockist_lead": "NO"},
        )
        no_contact = _row(
            website=None,
            evidence={
                "signals": ["direct_website"],
                "stockist_lead": "UNKNOWN",
                "stockist_lead_reasons": ["no_usable_contact_path"],
            },
        )
        self.assertEqual(failure_class(insufficient), "A_found_insufficient_evidence")
        self.assertEqual(failure_class(missing_id), "B_never_identified")
        self.assertEqual(failure_class(mistaken), "C_listing_mistaken_for_business")
        self.assertEqual(failure_class(no_contact), "D_identified_contact_missing")
        self.assertEqual(unknown_missing_evidence(insufficient), ["fashion/retail evidence"])
        self.assertEqual(unknown_missing_evidence(no_contact), ["contact path"])

    def test_seed_comparison_does_not_require_injected_seeds(self) -> None:
        organic = {
            "yes_leads": [
                {"business_name": "Rangeela Goa", "website": "https://rangeelagoa.com"},
                {"business_name": "Other Shop", "website": "https://othershop.example"},
            ]
        }
        seed_report = {
            "seeds": [
                {"seed_name": "Rangeela Goa", "potential_stockist": "NO", "stockist_lead": "YES"},
                {"seed_name": "Yellow House Parra", "potential_stockist": "UNKNOWN", "stockist_lead": "UNKNOWN"},
            ]
        }
        comparison = compare_seed_benchmark(
            organic,
            seed_report,
            ("Rangeela Goa", "Yellow House Parra"),
        )
        self.assertEqual(comparison["organic_leads_yes"], 2)
        self.assertEqual(comparison["seed_leads_yes"], 1)
        self.assertEqual(comparison["seed_names_found_as_organic_leads"], ["Rangeela Goa"])
        self.assertEqual(comparison["organic_leads_not_in_seed_list"], ["Other Shop"])
        self.assertEqual(comparison["seeds_not_found_as_organic_leads"], ["Yellow House Parra"])

    def test_report_module_does_not_hardcode_production_names(self) -> None:
        source = (SRC_DIR / "organic_lead_report.py").read_text(encoding="utf-8")
        self.assertNotIn("Rangeela Goa", source)
        self.assertNotIn("Yellow House Parra", source)
        self.assertNotIn("Rozina", source)


if __name__ == "__main__":
    unittest.main()
