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
            text=(
                "A concept store in Assagao, North Goa. Luxury clothing, "
                "home decor, gifts and furniture collections."
            ),
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
        page = _page(
            text="Designer studio in Goa. Our own collection of women's dresses."
        )
        result = assess_stockist_lead(row, page)
        self.assertEqual(result["stockist_lead"], "NO")
        self.assertIn("own_label_not_lead", result["reasons"])

    def test_directory_is_not_a_lead(self) -> None:
        row = _row(source_type="DIRECTORY", website=None)
        result = assess_stockist_lead(row, _page(text="Boutiques in Goa"))
        self.assertEqual(result["stockist_lead"], "NO")

    def test_missing_address_does_not_block_a_lead(self) -> None:
        row = _row(
            address=None,
            city="Goa",
            email="shop@example.com",
            website="https://shop.example",
        )
        page = _page(text="Fashion boutique in Goa with curated collections.")
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
        page = _page(
            text=(
                "Home to almost 50 independent designers, brands, artisans and makers "
                "in Aldona, North Goa."
            )
        )
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
            text=(
                "A concept store in Assagao, North Goa. Luxury clothing, home decor, "
                "gifts and furniture collections."
            )
        )
        attached = attach_stockist_lead(row, page)
        self.assertEqual(lead_of(attached), "YES")
        self.assertEqual(attached.evidence["ai_potential_stockist"], "NO")
        self.assertEqual(attached.physical_store, PhysicalStore.UNKNOWN)

    def test_listicle_publisher_is_not_a_lead(self) -> None:
        row = _row(
            business_name="Travel Blog",
            website="https://blog.example/5-cool-boutiques-visit-for-shopping-in-goa",
            source_url="https://blog.example/5-cool-boutiques-visit-for-shopping-in-goa",
        )
        page = _page(
            title="5 Cool Boutiques To Visit for Shopping in Goa",
            text="A shopping guide to boutiques in Goa.",
        )
        result = assess_stockist_lead(row, page)
        self.assertEqual(result["stockist_lead"], "NO")
        self.assertIn(result["entity"]["entity_relationship"], {"ARTICLE", "MEDIA"})

    def test_instagram_reel_is_not_a_lead(self) -> None:
        row = _row(
            business_name="Found a budget shop",
            website=None,
            instagram="https://instagram.com/reel/DROk1BME4m-",
            source_url="https://instagram.com/reel/DROk1BME4m-",
            source_type="SOCIAL",
            evidence={"signals": ["social_post"]},
        )
        result = assess_stockist_lead(row, _page(title="Found a budget shop"))
        self.assertEqual(result["stockist_lead"], "NO")
        self.assertEqual(result["entity"]["entity_relationship"], "SOCIAL_POST")

    def test_wrong_country_is_not_a_goa_lead(self) -> None:
        row = _row(
            business_name="Orchid Boutique",
            website="https://orchidboutique.ie/collections/goa-goa",
            source_url="https://orchidboutique.ie/collections/goa-goa",
            email="shop@orchidboutique.ie",
            city="Waterford",
        )
        page = _page(text="Women's boutique in Waterford, Ireland. Goa Goa collection.")
        result = assess_stockist_lead(row, page)
        self.assertEqual(result["stockist_lead"], "NO")
        self.assertEqual(result["entity"]["geographic_relevance"], "NO")

    def test_hotel_resort_boutique_is_not_an_automatic_lead(self) -> None:
        row = _row(
            business_name="Resort Boutique",
            website="https://heritage.example/heritage-village-resort-spa-goa/boutique-store/",
            source_url="https://heritage.example/heritage-village-resort-spa-goa/boutique-store/",
            email="stay@heritage.example",
            city="Goa",
        )
        page = _page(
            title="Boutique store at the resort",
            text="Hotel resort boutique in Cansaulim, Goa with designer labels.",
        )
        result = assess_stockist_lead(row, page)
        self.assertEqual(result["stockist_lead"], "UNKNOWN")
        self.assertEqual(result["entity"]["business_context"], "HOTEL_RESORT_BOUTIQUE")

    def test_social_profile_with_goa_and_retail_is_a_lead(self) -> None:
        row = _row(
            business_name="Sosa Goa Boutique",
            website=None,
            instagram="https://instagram.com/sosas.goaboutique",
            facebook="https://facebook.com/Sosasgoa",
            source_type="SOCIAL",
            source_url="https://instagram.com/sosas.goaboutique",
            evidence={"signals": ["social_profile"]},
        )
        result = assess_stockist_lead(row, _page(title="Sosa Goa Boutique"))
        self.assertEqual(result["stockist_lead"], "YES")

    def test_facebook_only_without_page_evidence_is_not_a_lead(self) -> None:
        row = _row(
            business_name="Chic Boutique Goa",
            website=None,
            facebook="https://facebook.com/ChicBoutiqueGoa",
            source_type="SOCIAL",
            source_url="https://facebook.com/ChicBoutiqueGoa",
            evidence={"signals": ["social_profile"]},
        )
        result = assess_stockist_lead(row, _page(title="Chic Boutique Goa"))
        self.assertEqual(result["stockist_lead"], "UNKNOWN")

    def test_production_logic_does_not_hardcode_names(self) -> None:
        for name in ("stockist_lead.py", "entity_quality.py"):
            source = (SRC_DIR / name).read_text(encoding="utf-8").lower()
            self.assertNotIn("rangeela", source)
            self.assertNotIn("yellow house", source)
            self.assertNotIn("paper boat", source)
            self.assertNotIn("villa mor", source)
            self.assertNotIn("rozina", source)
            self.assertNotIn("that goan girl", source)
            self.assertNotIn("hippie in heels", source)
            self.assertNotIn("orchid boutique", source)


if __name__ == "__main__":
    unittest.main()
