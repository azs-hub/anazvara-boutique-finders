"""Offline tests for the stockist lead layer."""

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
from content_extraction import PageEvidence
from stockist_lead import assess_stockist_lead, attach_stockist_lead, lead_of


def _page(**kwargs) -> PageEvidence:
    defaults = dict(
        source_url="https://example.com",
        final_url="https://example.com",
        domain="example.com",
        title="",
        meta_description=None,
        text="",
        headings=[],
        links=[],
    )
    defaults.update(kwargs)
    return PageEvidence(**defaults)


def _row(**kwargs) -> BusinessCandidate:
    defaults = dict(
        business_name="Example Store",
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
        evidence={"signals": ["direct_website"]},
        confidence=Confidence.MEDIUM,
    )
    defaults.update(kwargs)
    return BusinessCandidate(**defaults)


class StockistLeadTests(unittest.TestCase):
    def test_concept_store_with_contacts_is_a_lead(self) -> None:
        row = _row(
            email="hello@example.com",
            phone="+91 12345",
            instagram="https://instagram.com/example",
            physical_store=PhysicalStore.UNKNOWN,
            evidence={
                "signals": ["direct_website"],
                "ai_potential_stockist": "NO",
            },
        )
        page = _page(
            title="Concept Fashion and Homeware Store",
            text="A concept store. Luxury clothing, home decor, gifts and furniture collections.",
        )
        result = assess_stockist_lead(row, page)
        self.assertEqual(result["stockist_lead"], "YES")
        self.assertTrue(result["manual_review"])
        self.assertEqual(row.evidence["ai_potential_stockist"], "NO")

    def test_own_label_fashion_business_is_not_a_lead(self) -> None:
        row = _row(
            business_type=BusinessType.DESIGNER,
            women_fashion_relevance=Relevance.HIGH,
            email="studio@example.com",
            evidence={
                "signals": ["direct_website"],
                "ai_business_type": "DESIGNER",
                "ai": {"validated": {"designer_positioning": "OWN_LABEL", "carries_other_brands": "NO"}},
            },
        )
        page = _page(text="Designer studio. Our own collection of women's dresses.")
        result = assess_stockist_lead(row, page)
        self.assertEqual(result["stockist_lead"], "NO")
        self.assertIn("own_label_not_lead", result["reasons"])

    def test_directory_is_not_a_lead(self) -> None:
        row = _row(source_type="DIRECTORY", website=None)
        result = assess_stockist_lead(row, _page(text="Boutiques in Goa"))
        self.assertEqual(result["stockist_lead"], "NO")

    def test_missing_address_does_not_block_a_lead(self) -> None:
        row = _row(address=None, email="shop@example.com", website="https://shop.example")
        page = _page(text="Fashion boutique with curated collections.")
        result = assess_stockist_lead(row, page)
        self.assertEqual(result["stockist_lead"], "YES")
        self.assertIsNone(result["contacts"]["address"])

    def test_social_without_retail_evidence_stays_unknown(self) -> None:
        row = _row(
            website=None,
            instagram="https://instagram.com/someone",
            source_type="SOCIAL",
            evidence={"signals": ["social_profile"]},
        )
        result = assess_stockist_lead(row, _page(title="Someone", text=""))
        self.assertEqual(result["stockist_lead"], "UNKNOWN")

    def test_proven_stockist_with_website_is_a_lead(self) -> None:
        row = _row(
            evidence={
                "signals": ["direct_website"],
                "ai_potential_stockist": "YES",
                "ai_business_type": "MULTI_BRAND",
            }
        )
        page = _page(text="Home to almost 50 independent designers, brands, artisans and makers.")
        attached = attach_stockist_lead(row, page)
        self.assertEqual(lead_of(attached), "YES")
        self.assertEqual(attached.evidence["ai_potential_stockist"], "YES")

    def test_fashion_relevance_alone_is_not_a_lead(self) -> None:
        row = _row(
            women_fashion_relevance=Relevance.HIGH,
            website="https://studio.example",
            evidence={"signals": ["direct_website"]},
        )
        result = assess_stockist_lead(row, _page(text="Women's dresses and studio appointments."))
        self.assertEqual(result["stockist_lead"], "UNKNOWN")

    def test_designer_type_with_concept_store_evidence_is_a_lead(self) -> None:
        row = _row(
            business_type=BusinessType.DESIGNER,
            physical_store=PhysicalStore.UNKNOWN,
            email="hello@example.com",
            evidence={
                "signals": ["direct_website"],
                "ai_potential_stockist": "NO",
                "ai_business_type": "UNKNOWN",
            },
        )
        page = _page(
            text="A concept store. Luxury clothing, home decor, gifts and furniture collections."
        )
        attached = attach_stockist_lead(row, page)
        self.assertEqual(lead_of(attached), "YES")
        self.assertEqual(attached.evidence["ai_potential_stockist"], "NO")
        self.assertEqual(attached.physical_store, PhysicalStore.UNKNOWN)

    def test_production_logic_does_not_hardcode_names(self) -> None:
        source = (SRC_DIR / "stockist_lead.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("rangeela", source)
        self.assertNotIn("yellow house", source)
        self.assertNotIn("paper boat", source)
        self.assertNotIn("villa mor", source)
        self.assertNotIn("rozina", source)


if __name__ == "__main__":
    unittest.main()
