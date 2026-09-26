"""Offline tests for benchmark-only Goa seed identity resolution."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from benchmark_seeds import (
    GOA_STOCKIST_SEEDS,
    ORIGIN_DIRECT,
    ORIGIN_SEED,
    VERIFIED_GOOGLE,
    VERIFIED_INSTAGRAM,
    VERIFIED_WEBSITE,
    SeedSpec,
    attach_seed_provenance,
    domain_matches_seed,
    handle_matches_seed,
    is_usable_official_url,
    looks_like_seed_stockist_page,
    official_website_identity_match,
    page_mentions_seed,
    seed_metrics,
    seed_tokens,
    verify_google_business,
    verify_social_identity,
)
from business_candidates import (
    UNKNOWN,
    BusinessCandidate,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
)
from classification import ResultType, classify_url, is_google_maps_url
from content_extraction import PageEvidence


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


def _row() -> BusinessCandidate:
    return BusinessCandidate(
        business_name="Example",
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
        evidence={},
        confidence=Confidence.LOW,
    )


class SeedSpecTests(unittest.TestCase):
    def test_eight_goa_seeds_exist(self) -> None:
        names = [seed.seed_name for seed in GOA_STOCKIST_SEEDS]
        self.assertEqual(len(names), 8)
        self.assertIn("Yellow House Parra", names)
        self.assertIn("Paper Boat Collective", names)
        self.assertIn("Rangeela Goa", names)
        self.assertNotIn("Villa Mor", names)

    def test_known_social_identities_are_supplied(self) -> None:
        by_name = {seed.seed_name: seed for seed in GOA_STOCKIST_SEEDS}
        self.assertEqual(by_name["Syne Goa"].facebook, "https://www.facebook.com/synegoa/")
        self.assertEqual(
            by_name["Rangeen Goa"].instagram, "https://www.instagram.com/rangeengoa/"
        )
        self.assertEqual(
            by_name["Paper Boat Collective"].instagram,
            "https://www.instagram.com/paperboatcollective/",
        )
        self.assertIsNone(by_name["Rangeela Goa"].instagram)

    def test_seeds_do_not_set_production_stockist(self) -> None:
        source = (SRC_DIR / "local_llm.py").read_text(encoding="utf-8")
        self.assertNotIn("Yellow House Parra", source)
        self.assertNotIn("Paper Boat Collective", source)
        self.assertNotIn("The Good Life Goa", source)
        self.assertNotIn("Rangeela Goa", source)


class OfficialUrlTests(unittest.TestCase):
    def test_directory_article_and_marketplaces_are_rejected(self) -> None:
        self.assertFalse(is_usable_official_url("https://justdial.com/Goa/Yellow-House"))
        self.assertFalse(is_usable_official_url("https://vogue.in/content/goa-shops"))
        self.assertFalse(is_usable_official_url("https://instagram.com/rangeelagoa"))
        self.assertFalse(is_usable_official_url("https://www.airbnb.com/rooms/123"))
        self.assertFalse(is_usable_official_url("https://shopee.in/sasha-shop"))
        self.assertFalse(is_usable_official_url("https://www.superstock.com/image/1"))
        self.assertFalse(is_usable_official_url("https://www.google.com/maps/place/Syne"))
        self.assertFalse(is_usable_official_url("https://bizgoa.in/listings/the-good-life-goa"))
        self.assertFalse(is_usable_official_url("https://makemytrip.com/hotels/sosa_villa"))
        self.assertFalse(
            is_usable_official_url("https://paper-boat-collective-goa.wheree.com")
        )
        self.assertTrue(is_usable_official_url("https://rangeelagoa.com"))


class NameMatchTests(unittest.TestCase):
    def test_rangeen_does_not_match_rangeela(self) -> None:
        seed = SeedSpec("Rangeen Goa")
        page = _page(title="Rangeela Goa concept store", text="Rangeela Assagao")
        self.assertFalse(page_mentions_seed(seed, page))

    def test_yellow_house_matches_site_copy(self) -> None:
        seed = SeedSpec("Yellow House Parra")
        page = _page(
            title="Yellow House",
            text="Yellow House Parra is home to independent designers.",
        )
        self.assertTrue(page_mentions_seed(seed, page))

    def test_mention_only_page_is_not_official_website(self) -> None:
        seed = SeedSpec("Paper Boat Collective")
        page = _page(
            title="Paper Boat Collective",
            og_site_name="The Good Doll",
            text="Visit Paper Boat Collective while in Goa.",
        )
        self.assertTrue(page_mentions_seed(seed, page, extra="Paper Boat Collective"))
        self.assertFalse(
            official_website_identity_match(
                seed,
                page,
                url="https://thegooddoll.in/pages/paperboat-collective",
                extra="Paper Boat Collective",
            )
        )
        self.assertFalse(
            official_website_identity_match(
                seed,
                _page(title="Syne Coutinho our favorite designer in Goa"),
                url="https://myfantasticindia.com/syne-coutinho-our-favorite-designer-in-goa",
                extra="Syne Coutinho our favorite designer in Goa",
            )
        )

    def test_domain_and_title_can_verify_official_website(self) -> None:
        seed = SeedSpec("Yellow House Parra", aliases=("Yellow House",))
        self.assertTrue(domain_matches_seed(seed, "https://yellowhouseparra.in"))
        page = _page(title="Yellow House Parra", og_site_name="Yellow House")
        self.assertTrue(
            official_website_identity_match(
                seed, page, url="https://yellowhouseparra.in", extra="Yellow House Parra"
            )
        )
        self.assertFalse(
            official_website_identity_match(
                SeedSpec("The Good Life Goa", aliases=("The Good Life",)),
                _page(title="The Good Life Gift Boutique"),
                url="https://thegoodlifeboutique.com",
                extra="The Good Life Gift Boutique",
            )
        )

    def test_sasha_tokens_ignore_shop_words(self) -> None:
        self.assertEqual(seed_tokens("Sasha's Shop"), ["sasha"])


class SocialIdentityTests(unittest.TestCase):
    def test_official_instagram_handle_is_accepted(self) -> None:
        seed = SeedSpec("Rangeen Goa", aliases=("Rangeen",))
        ok, signals = verify_social_identity(
            seed,
            url="https://www.instagram.com/rangeengoa/",
            title="Rangeen Goa",
            snippet="Boutique in Goa",
            supplied=True,
        )
        self.assertTrue(ok)
        self.assertIn("handle_match", signals)

    def test_similar_instagram_without_location_is_rejected(self) -> None:
        seed = SeedSpec("Rangeen Goa")
        ok, _signals = verify_social_identity(
            seed,
            url="https://www.instagram.com/rangeenstyle/",
            title="Rangeen Style",
            snippet="Fashion account",
        )
        self.assertFalse(ok)

    def test_sasha_handle_variant_matches(self) -> None:
        seed = SeedSpec(
            "Sasha's Shop",
            aliases=("Sacha the Shop Keeper",),
        )
        self.assertTrue(
            handle_matches_seed(seed, "https://www.instagram.com/sachatheshopkeeper/")
        )


class GoogleIdentityTests(unittest.TestCase):
    def test_maps_url_is_not_a_website(self) -> None:
        url = "https://www.google.com/maps/place/Villa+Mor+Goa"
        self.assertTrue(is_google_maps_url(url))
        self.assertEqual(classify_url(url), ResultType.DIRECTORY)
        self.assertFalse(is_usable_official_url(url))

    def test_maps_needs_name_and_goa(self) -> None:
        seed = SeedSpec("Syne Goa")
        ok, _signals = verify_google_business(
            seed,
            url="https://www.google.com/maps/place/Syne+Goa",
            title="Syne Goa",
            snippet="Clothing store in Panjim, Goa",
        )
        self.assertTrue(ok)
        rejected, _ = verify_google_business(
            seed,
            url="https://www.google.com/maps/place/Syne+Delhi",
            title="Syne Delhi",
            snippet="Store in Delhi",
        )
        self.assertFalse(rejected)


class StockistPageTests(unittest.TestCase):
    def test_designers_and_faq_are_useful(self) -> None:
        self.assertTrue(looks_like_seed_stockist_page("https://shop.example/designers"))
        self.assertTrue(looks_like_seed_stockist_page("https://shop.example/faq", "Sell with us"))
        self.assertFalse(looks_like_seed_stockist_page("https://shop.example/cart"))


class ProvenanceTests(unittest.TestCase):
    def test_expected_stockist_is_metadata_only(self) -> None:
        from benchmark_seeds import SeedResolution

        resolution = SeedResolution(
            seed=SeedSpec("Paper Boat Collective", reference_expected_stockist="YES"),
            official_website_verified=True,
            website="https://paperboat.example",
            instagram_url="https://instagram.com/paperboatcollective",
            instagram_verified=True,
            identity_sources=[VERIFIED_WEBSITE, VERIFIED_INSTAGRAM],
            entity_verified_from=f"{VERIFIED_WEBSITE} + {VERIFIED_INSTAGRAM}",
        )
        attached = attach_seed_provenance(_row(), resolution)
        self.assertEqual(attached.evidence["entity_origin"], ORIGIN_DIRECT)
        self.assertEqual(attached.evidence["seed_origin"], ORIGIN_SEED)
        self.assertEqual(attached.evidence["reference_expected_stockist"], "YES")
        self.assertEqual(
            attached.evidence["entity_verified_from"],
            f"{VERIFIED_WEBSITE} + {VERIFIED_INSTAGRAM}",
        )
        self.assertNotIn("potential_stockist", attached.evidence)
        self.assertEqual(attached.business_type, BusinessType.UNKNOWN)

    def test_social_only_keeps_seed_origin(self) -> None:
        from benchmark_seeds import SeedResolution

        resolution = SeedResolution(
            seed=SeedSpec("Rangeen Goa"),
            instagram_url="https://instagram.com/rangeengoa",
            instagram_verified=True,
            identity_sources=[VERIFIED_INSTAGRAM],
            entity_verified_from=VERIFIED_INSTAGRAM,
        )
        attached = attach_seed_provenance(replace(_row(), website=None), resolution)
        self.assertEqual(attached.evidence["entity_origin"], ORIGIN_SEED)
        self.assertEqual(attached.evidence["entity_verified_from"], VERIFIED_INSTAGRAM)
        self.assertEqual(attached.instagram, "https://instagram.com/rangeengoa")
        self.assertIsNone(attached.website)

    def test_metrics_count_identity_sources(self) -> None:
        rows = [
            {
                "discovered": True,
                "organic_hit": False,
                "official_website_verified": True,
                "instagram": "https://instagram.com/rangeela",
                "facebook": None,
                "google_business_verified": False,
                "identity_verified": True,
                "identity_sources": [VERIFIED_WEBSITE, VERIFIED_INSTAGRAM],
                "qwen_reached": True,
                "potential_stockist": "YES",
                "validation": "passed",
            },
            {
                "discovered": True,
                "organic_hit": True,
                "official_website_verified": False,
                "instagram": "https://instagram.com/rangeengoa",
                "facebook": None,
                "google_business_verified": True,
                "identity_verified": True,
                "identity_sources": [VERIFIED_INSTAGRAM, VERIFIED_GOOGLE],
                "qwen_reached": True,
                "potential_stockist": "UNKNOWN",
                "validation": "n/a",
            },
        ]
        metrics = seed_metrics(rows)
        self.assertEqual(metrics["seeds_total"], 2)
        self.assertEqual(metrics["seeds_with_official_website"], 1)
        self.assertEqual(metrics["seeds_with_official_instagram"], 2)
        self.assertEqual(metrics["seeds_with_verified_google_business"], 1)
        self.assertEqual(metrics["seeds_with_any_verified_identity"], 2)
        self.assertEqual(metrics["seeds_with_multiple_identity_sources"], 2)
        self.assertEqual(metrics["seeds_stockist_yes"], 1)
        self.assertEqual(metrics["seeds_without_official_website"], 1)


if __name__ == "__main__":
    unittest.main()
