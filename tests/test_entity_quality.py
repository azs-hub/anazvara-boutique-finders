"""Offline tests for the entity-quality gate."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from entity_quality import (
    assess_entity_quality,
    is_social_post_url,
    looks_like_media_or_listicle,
)


class EntityQualityTests(unittest.TestCase):
    def test_official_goa_concept_store_is_self_business(self) -> None:
        result = assess_entity_quality(
            url="https://shop.example",
            title="Concept store",
            text="A concept store in Assagao, North Goa. Luxury clothing collections.",
            source_type="WEBSITE",
            signals=["direct_website"],
            name="Example Store",
            city="Goa",
            website="https://shop.example",
            instagram="https://instagram.com/examplestore",
        )
        self.assertEqual(result["entity_relationship"], "SELF")
        self.assertEqual(result["entity_is_business"], "YES")
        self.assertEqual(result["geographic_relevance"], "YES")
        self.assertFalse(result["excluded_from_lead_eval"])

    def test_listicle_is_article_not_business(self) -> None:
        result = assess_entity_quality(
            url="https://blog.example/5-cool-boutiques-visit-for-shopping-in-goa",
            title="5 Cool Boutiques To Visit for Shopping in Goa",
            text="A shopping guide.",
            source_type="WEBSITE",
            name="Travel Blog",
            website="https://blog.example/5-cool-boutiques-visit-for-shopping-in-goa",
        )
        self.assertIn(result["entity_relationship"], {"ARTICLE", "MEDIA"})
        self.assertEqual(result["entity_is_business"], "NO")
        self.assertTrue(result["excluded_from_lead_eval"])

    def test_reel_url_is_social_post(self) -> None:
        self.assertTrue(is_social_post_url("https://instagram.com/reel/abc123"))
        self.assertFalse(is_social_post_url("https://instagram.com/sosas.goaboutique"))
        result = assess_entity_quality(
            url="https://instagram.com/reel/abc123",
            title="Found a budget shop",
            source_type="SOCIAL",
            name="Found a budget shop",
            instagram="https://instagram.com/reel/abc123",
        )
        self.assertEqual(result["entity_relationship"], "SOCIAL_POST")
        self.assertEqual(result["entity_is_business"], "NO")

    def test_ireland_site_is_not_goa(self) -> None:
        result = assess_entity_quality(
            url="https://shop.ie/collections/goa-goa",
            text="Boutique in Waterford, Ireland.",
            source_type="WEBSITE",
            name="Example Boutique",
            city="Waterford",
            website="https://shop.ie/collections/goa-goa",
        )
        self.assertEqual(result["geographic_relevance"], "NO")
        self.assertTrue(result["excluded_from_lead_eval"])

    def test_hotel_context_is_not_independent_retail(self) -> None:
        result = assess_entity_quality(
            url="https://stay.example/heritage-village-resort-spa-goa/boutique-store/",
            title="Resort boutique",
            text="Hotel resort boutique in Cansaulim, Goa.",
            source_type="WEBSITE",
            name="Resort Boutique",
            city="Goa",
            website="https://stay.example/heritage-village-resort-spa-goa/boutique-store/",
        )
        self.assertEqual(result["business_context"], "HOTEL_RESORT_BOUTIQUE")
        self.assertTrue(result["excluded_from_lead_eval"])

    def test_media_path_is_detected(self) -> None:
        self.assertTrue(
            looks_like_media_or_listicle(
                url="https://news.example/my-goa/shopping-in-goa/modish",
                title="A fashion haven for women in Goa",
            )
        )

    def test_module_does_not_hardcode_business_names(self) -> None:
        source = (SRC_DIR / "entity_quality.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("rangeela", source)
        self.assertNotIn("that goan girl", source)
        self.assertNotIn("hippie in heels", source)
        self.assertNotIn("gomantak", source)
        self.assertNotIn("orchid boutique", source)


if __name__ == "__main__":
    unittest.main()
