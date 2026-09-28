"""Offline tests for geography, article expansion, and stockist fit."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from business_candidates import merge_in_memory_duplicates
from candidates import Candidate
from classification import ResultType
from content_extraction import ExtractedLink, PageEvidence
from geography import ExpectedPlace
from lead_export import CSV_COLUMNS
from page_expansion import expand_from_page
from stockist_fit import assess_stockist_fit


def _place(city: str, state: str) -> ExpectedPlace:
    return ExpectedPlace(city=city, state=state, country="India")


def _page(*, title: str, text: str, links: list | None = None, headings: list | None = None) -> PageEvidence:
    return PageEvidence(
        source_url="https://magazine.example/guide",
        final_url="https://magazine.example/guide",
        domain="magazine.example",
        title=title,
        meta_description=None,
        text=text,
        headings=headings or [],
        links=links or [],
    )


def _article(title: str, text: str) -> Candidate:
    return Candidate(
        title=title,
        url="https://magazine.example/guide",
        normalized_url="https://magazine.example/guide",
        domain="magazine.example",
        snippet="",
        result_type=ResultType.ARTICLE,
        search_query="best boutiques",
        search_source="test",
    )


GOA = (
    "Visit us at 18 Assagao Road, Bardez, Goa 403507. Opening hours 11:00 to 19:00. "
    "Women's contemporary fashion from independent designers. Slow fashion in linen "
    "and Indian craftsmanship. Curated premium boutique. Phone +91 9811111111."
)
MUMBAI = (
    "Visit us at 4 Perry Cross Road, Bandra West, Mumbai 400050. Opening hours daily. "
    "Women's contemporary fashion and independent designers. Curated premium store. "
    "Phone +91 9822222222."
)
DELHI = (
    "Our store is at 21 Shahpur Jat, New Delhi 110049. Opening hours 11 to 7. "
    "Women's contemporary fashion, independent designers, handcrafted linen. "
    "Phone +91 9833333333."
)


class GeographyAndFitTests(unittest.TestCase):
    def test_goa_physical_store(self) -> None:
        result = assess_stockist_fit(
            GOA,
            business_name="Coastal Edit",
            city="Goa",
            address="18 Assagao Road, Bardez, Goa 403507",
            phone="+91 9811111111",
            website="https://coastal.example",
            expected=_place("Goa", "Goa"),
        )
        self.assertEqual(result["physical_store"], "YES")
        self.assertEqual(result["geography"], "YES")
        self.assertIn(result["status"], {"LEAD", "REVIEW"})
        self.assertIn("assagao", result["explanation"].casefold())

    def test_mumbai_physical_store(self) -> None:
        result = assess_stockist_fit(
            MUMBAI,
            business_name="Bandra Edit",
            city="Mumbai",
            address="4 Perry Cross Road, Bandra West, Mumbai 400050",
            website="https://bandra.example",
            expected=_place("Mumbai", "Maharashtra"),
        )
        self.assertEqual(result["geography"], "YES")
        self.assertEqual(result["physical_store"], "YES")
        self.assertNotEqual(result["status"], "EXCLUDED")

    def test_delhi_physical_store(self) -> None:
        result = assess_stockist_fit(
            DELHI,
            business_name="Shahpur Atelier",
            city="Delhi",
            address="21 Shahpur Jat, New Delhi 110049",
            website="https://shahpur.example",
            expected=_place("Delhi", "Delhi"),
        )
        self.assertEqual(result["geography"], "YES")
        self.assertEqual(result["physical_store"], "YES")
        self.assertNotEqual(result["status"], "EXCLUDED")

    def test_online_only_fashion_brand_is_excluded(self) -> None:
        result = assess_stockist_fit(
            "Online-only women's contemporary fashion brand. We ship across India.",
            business_name="Screen Label",
            website="https://screen.example",
            expected=_place("Mumbai", "Maharashtra"),
        )
        self.assertEqual(result["physical_store"], "NO")
        self.assertEqual(result["status"], "EXCLUDED")
        self.assertIn("online-only", result["exclusions"])

    def test_own_label_physical_store_is_excluded(self) -> None:
        result = assess_stockist_fit(
            "Visit us at 9 C Scheme, Jaipur 302001. Women's clothing. We only sell our own label.",
            business_name="Studio Own",
            city="Jaipur",
            address="9 C Scheme, Jaipur 302001",
            website="https://studio-own.example",
            expected=_place("Jaipur", "Rajasthan"),
        )
        self.assertEqual(result["multi_brand"], "NO")
        self.assertEqual(result["status"], "EXCLUDED")
        self.assertIn("own-brand-only", result["exclusions"])

    def test_genuine_multi_brand_boutique_is_a_lead(self) -> None:
        result = assess_stockist_fit(
            "Visit us at 18 Assagao Road, Goa 403507. We stock multiple designers. "
            "Women's contemporary fashion, independent designers, linen and Indian craftsmanship. "
            "Curated premium boutique.",
            business_name="Multi House",
            city="Goa",
            address="18 Assagao Road, Goa 403507",
            website="https://multi-house.example",
            expected=_place("Goa", "Goa"),
        )
        self.assertEqual(result["multi_brand"], "YES")
        self.assertEqual(result["anazvara_fit"], "HIGH")
        self.assertEqual(result["status"], "LEAD")

    def test_concept_store_can_be_a_lead(self) -> None:
        result = assess_stockist_fit(
            "Concept store at 4 Perry Cross Road, Bandra, Mumbai 400050. Opening hours 11 to 8. "
            "Curated fashion store. Women's contemporary fashion and independent designers. "
            "Slow fashion, linen, Indian craftsmanship.",
            business_name="Concept Room",
            city="Mumbai",
            address="4 Perry Cross Road, Bandra, Mumbai 400050",
            website="https://concept-room.example",
            expected=_place("Mumbai", "Maharashtra"),
        )
        self.assertEqual(result["retail_model"], "concept store")
        self.assertGreaterEqual(result["concept_store_score"], 3)
        self.assertEqual(result["status"], "LEAD")

    def test_ambiguous_boutique_without_website_is_review(self) -> None:
        result = assess_stockist_fit(
            "Neighbourhood boutique. No site listed yet.",
            business_name="Lane Boutique",
            city="Pune",
            expected=_place("Pune", "Maharashtra"),
        )
        self.assertEqual(result["multi_brand"], "UNKNOWN")
        self.assertNotEqual(result["multi_brand"], "NO")
        self.assertEqual(result["status"], "REVIEW")

    def test_unknown_multi_brand_is_not_treated_as_no(self) -> None:
        result = assess_stockist_fit(
            "Visit us at 18 Assagao Road, Goa 403507. Women's contemporary fashion, "
            "slow fashion, linen and Indian craftsmanship. Curated premium boutique.",
            business_name="Unlisted Edit",
            city="Goa",
            address="18 Assagao Road, Goa 403507",
            website="https://unlisted.example",
            expected=_place("Goa", "Goa"),
        )
        self.assertEqual(result["multi_brand"], "UNKNOWN")
        self.assertEqual(result["physical_store"], "YES")
        self.assertEqual(result["status"], "REVIEW")

    def test_business_in_wrong_city_is_excluded(self) -> None:
        result = assess_stockist_fit(
            MUMBAI,
            business_name="Bandra Edit",
            city="Mumbai",
            address="4 Perry Cross Road, Bandra West, Mumbai 400050",
            website="https://bandra.example",
            expected=_place("Goa", "Goa"),
        )
        self.assertEqual(result["geography"], "NO")
        self.assertEqual(result["status"], "EXCLUDED")


class ArticleExpansionTests(unittest.TestCase):
    def test_article_with_ten_boutiques_becomes_businesses(self) -> None:
        names = [
            "Rangeela",
            "Mora House",
            "Salt Store",
            "Paper Room",
            "Lime Edit",
            "Ivory Collective",
            "North Atelier",
            "Sable Studio",
            "Willow Rack",
            "Cedar Concept",
        ]
        text = " ".join(f"{index}. {name}, Assagao" for index, name in enumerate(names, start=1))
        rows = expand_from_page(
            _article("The 15 best boutiques in Goa", text),
            page_evidence=_page(title="The 15 best boutiques in Goa", text=text),
            expected=_place("Goa", "Goa"),
        )
        found = {row.business_name for row in rows}
        self.assertTrue(set(names).issubset(found))
        self.assertNotIn("The 15 best boutiques in Goa", found)
        self.assertTrue(all(row.evidence["discovered_from"] == "article" for row in rows))
        self.assertTrue(all(row.evidence["discovery_source_url"].endswith("/guide") for row in rows))

    def test_directory_extracts_multiple_businesses(self) -> None:
        links = [
            ExtractedLink(
                url=f"https://shop-{index}.example/",
                normalized_url=f"https://shop-{index}.example/",
                anchor_text=name,
                useful=True,
                external=True,
            )
            for index, name in enumerate(("First Rack", "Second Rack", "Third Rack"), start=1)
        ]
        candidate = Candidate(
            title="Fashion directory",
            url="https://directory.example/delhi",
            normalized_url="https://directory.example/delhi",
            domain="directory.example",
            snippet="",
            result_type=ResultType.DIRECTORY,
            search_query="designer boutiques Delhi",
            search_source="test",
        )
        page = PageEvidence(
            source_url=candidate.normalized_url,
            final_url=candidate.normalized_url,
            domain="directory.example",
            title="Fashion directory",
            meta_description=None,
            text="Shops in Delhi.",
            headings=[],
            links=links,
        )
        rows = expand_from_page(candidate, page_evidence=page, expected=_place("Delhi", "Delhi"))
        self.assertEqual(
            {row.business_name for row in rows},
            {"First Rack", "Second Rack", "Third Rack"},
        )
        self.assertTrue(all(row.city == "Delhi" for row in rows))
        self.assertTrue(all(row.evidence["discovery_source_type"] == "directory" for row in rows))

    def test_same_business_from_multiple_sources_merges(self) -> None:
        from business_candidates import (
            BusinessCandidate,
            BusinessType,
            Confidence,
            PhysicalStore,
            Relevance,
            UNKNOWN,
        )

        def row(source: str, website: str | None, discovered: str) -> BusinessCandidate:
            return BusinessCandidate(
                business_name="Rangeela",
                website=website,
                instagram=None,
                facebook=None,
                whatsapp=None,
                phone=None,
                email=None,
                address=None,
                city="Goa",
                source_url=source,
                source_type="WEBSITE" if website else "ARTICLE",
                business_type=BusinessType.BOUTIQUE,
                fashion_relevance=Relevance.UNKNOWN,
                women_fashion_relevance=Relevance.UNKNOWN,
                physical_store=PhysicalStore.UNKNOWN,
                evidence={
                    "discovery_sources": [{"type": discovered, "url": source, "title": discovered}],
                    "discovered_from": discovered,
                },
                confidence=Confidence.MEDIUM,
            )

        merged = merge_in_memory_duplicates(
            [
                row("https://rangeela.example", "https://rangeela.example", "search"),
                row("https://magazine.example/guide", None, "article"),
                row("https://instagram.com/rangeelagoa", None, "instagram"),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].website, "https://rangeela.example")
        kinds = {item["type"] for item in merged[0].evidence["discovery_sources"]}
        self.assertEqual(kinds, {"search", "article", "instagram"})
        self.assertIsNot(UNKNOWN, None)

    def test_csv_keeps_existing_columns_and_adds_evidence(self) -> None:
        for name in (
            "business_name",
            "export_group",
            "stockist_lead",
            "physical_store",
            "discovered_by_queries",
        ):
            self.assertIn(name, CSV_COLUMNS)
        for name in (
            "retail_model",
            "anazvara_fit_score",
            "discovery_source_url",
            "evidence_physical_store",
            "reason",
            "state",
            "country",
        ):
            self.assertIn(name, CSV_COLUMNS)


if __name__ == "__main__":
    unittest.main()
