"""Offline tests for deterministic BusinessCandidate identification."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from business_candidates import (
    UNKNOWN,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
    identify_business_candidates,
    merge_in_memory_duplicates,
)
from candidates import Candidate
from classification import ResultType
from content_extraction import BusinessSignals, ExtractedLink, PageEvidence


def _candidate(**kwargs) -> Candidate:
    defaults = dict(
        title="",
        url="https://example.com",
        normalized_url="https://example.com",
        domain="example.com",
        snippet="",
        result_type=ResultType.WEBSITE,
        search_query="women's fashion boutique Mumbai",
        search_source="test",
    )
    defaults.update(kwargs)
    return Candidate(**defaults)


def _evidence(**kwargs) -> PageEvidence:
    defaults = dict(
        source_url="https://example.com",
        final_url="https://example.com",
        domain="example.com",
        title=None,
        meta_description=None,
        text="",
        headings=[],
        links=[],
    )
    defaults.update(kwargs)
    return PageEvidence(**defaults)


def _link(url: str, anchor: str, *, external: bool = True) -> ExtractedLink:
    return ExtractedLink(
        url=url,
        normalized_url=url,
        anchor_text=anchor,
        useful=True,
        external=external,
    )


class BusinessCandidateTests(unittest.TestCase):
    def test_direct_website_boutique_name(self) -> None:
        rows = identify_business_candidates(
            _candidate(title="Rozina - Women's Boutique Mumbai"),
            _evidence(
                title="Rozina - Women's Boutique Mumbai",
                headings=["Rozina Women's Boutique"],
                text="Fashion boutique for women's clothing. Visit us at our store.",
            ),
            BusinessSignals(),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].business_name, "Rozina")
        self.assertEqual(rows[0].business_type, BusinessType.BOUTIQUE)
        self.assertEqual(rows[0].source_type, "WEBSITE")

    def test_womens_fashion_signals(self) -> None:
        rows = identify_business_candidates(
            _candidate(),
            _evidence(
                title="Rozina",
                text="Women's clothing boutique with dresses and sarees.",
            ),
            BusinessSignals(),
        )
        self.assertEqual(rows[0].women_fashion_relevance, Relevance.HIGH)
        self.assertEqual(rows[0].fashion_relevance, Relevance.HIGH)

    def test_physical_store_address(self) -> None:
        rows = identify_business_candidates(
            _candidate(),
            _evidence(
                title="Rozina",
                text="Visit us at our boutique. Women's clothing boutique.",
            ),
            BusinessSignals(
                address_candidates=["12 Linking Road, Bandra West, Mumbai 400050"],
            ),
        )
        self.assertEqual(rows[0].physical_store, PhysicalStore.YES)
        self.assertEqual(rows[0].city, "Mumbai")
        self.assertEqual(rows[0].address, "12 Linking Road, Bandra West, Mumbai 400050")

    def test_multiple_cities_not_guessed(self) -> None:
        rows = identify_business_candidates(
            _candidate(),
            _evidence(
                title="Our Stores",
                text="Stores in Mumbai Delhi Bangalore Jaipur. Fashion boutique.",
            ),
            BusinessSignals(
                city_mentions=["Mumbai", "Delhi", "Bangalore", "Jaipur"],
                address_candidates=[
                    "Bandra, Mumbai 400050",
                    "Defence Colony, Delhi 110024",
                ],
            ),
        )
        self.assertEqual(rows[0].city, UNKNOWN)

    def test_insufficient_evidence_unknown(self) -> None:
        rows = identify_business_candidates(
            _candidate(title="Home"),
            _evidence(title="Home", text="Welcome to our website."),
            BusinessSignals(),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].business_name, UNKNOWN)
        self.assertEqual(rows[0].business_type, BusinessType.UNKNOWN)
        self.assertEqual(rows[0].fashion_relevance, Relevance.UNKNOWN)
        self.assertEqual(rows[0].women_fashion_relevance, Relevance.UNKNOWN)
        self.assertEqual(rows[0].physical_store, PhysicalStore.UNKNOWN)
        self.assertEqual(rows[0].confidence, Confidence.LOW)

    def test_ecommerce_chrome_titles_are_never_business_names(self) -> None:
        for title in (
            "Item added to your cart",
            "Order Summary",
            "Shopping Cart",
            "My Account",
            "Login",
            "Checkout",
            "Shop Now",
            "Country/region",
            "Follow On Instagram",
            "Women's Ethnic Clothing Online",
            "Media Coverage",
            "Franchisee",
            "Shipping",
            "Create Free Account Now!",
            "Disclaimer",
        ):
            with self.subTest(title=title):
                row = identify_business_candidates(
                    _candidate(title=title),
                    _evidence(title=title),
                    BusinessSignals(),
                )[0]
                self.assertEqual(row.business_name, UNKNOWN)

    def test_directory_extracts_multiple_businesses(self) -> None:
        rows = identify_business_candidates(
            _candidate(
                result_type=ResultType.DIRECTORY,
                url="https://lbb.in/mumbai/boutiques",
                normalized_url="https://lbb.in/mumbai/boutiques",
                domain="lbb.in",
            ),
            _evidence(
                title="Boutiques in Mumbai | LBB",
                domain="lbb.in",
                source_url="https://lbb.in/mumbai/boutiques",
                final_url="https://lbb.in/mumbai/boutiques",
                links=[
                    _link("https://kalkifashion.com/", "KALKI Fashion"),
                    _link("https://aashniandco.com/", "Aashni + Co"),
                    _link("https://flauntit.example/", "Flaunt It Boutique"),
                ],
            ),
            BusinessSignals(),
        )
        names = {row.business_name for row in rows}
        self.assertEqual(names, {"KALKI Fashion", "Aashni + Co", "Flaunt It Boutique"})
        self.assertTrue(all(row.source_type == "DIRECTORY" for row in rows))
        self.assertTrue(all(row.evidence["name_source"] == "anchor_text" for row in rows))

    def test_plain_boutiques_in_city_title_is_treated_as_roundup(self) -> None:
        rows = identify_business_candidates(
            _candidate(title="Boutiques in Mumbai"),
            _evidence(
                title="Boutiques in Mumbai",
                text="A guide to fashion boutiques and stores.",
                links=[_link("https://rozina.example/", "Rozina")],
            ),
            BusinessSignals(),
        )
        self.assertEqual([row.business_name for row in rows], ["Rozina"])
        self.assertNotEqual(rows[0].website, "https://example.com")

    def test_search_title_detects_roundup_hidden_by_page_chrome(self) -> None:
        rows = identify_business_candidates(
            _candidate(
                title="Top Fashion Boutiques Mumbai",
                url="https://blog.example.com/list",
                normalized_url="https://blog.example.com/list",
                domain="blog.example.com",
            ),
            _evidence(
                title="Create Free Account Now!",
                source_url="https://blog.example.com/list",
                final_url="https://blog.example.com/list",
                domain="blog.example.com",
                links=[
                    _link("https://example.com/", "Publisher Shop"),
                    _link("https://rozina.example/", "Rozina"),
                ],
            ),
            BusinessSignals(),
        )
        self.assertEqual([row.business_name for row in rows], ["Rozina"])

    def test_directory_excludes_navigation(self) -> None:
        rows = identify_business_candidates(
            _candidate(
                result_type=ResultType.DIRECTORY,
                url="https://lbb.in/list",
                normalized_url="https://lbb.in/list",
                domain="lbb.in",
            ),
            _evidence(
                domain="lbb.in",
                source_url="https://lbb.in/list",
                final_url="https://lbb.in/list",
                links=[
                    _link("https://lbb.in/login", "Login", external=False),
                    _link("https://other.example/login", "Login"),
                    _link("https://instagram.com/someone", "Instagram"),
                    _link("https://realshop.example/", "Real Shop"),
                ],
            ),
            BusinessSignals(),
        )
        self.assertEqual([row.business_name for row in rows], ["Real Shop"])

    def test_article_extracts_linked_businesses(self) -> None:
        rows = identify_business_candidates(
            _candidate(
                result_type=ResultType.ARTICLE,
                url="https://vogue.in/article/mumbai-boutiques",
                normalized_url="https://vogue.in/article/mumbai-boutiques",
                domain="vogue.in",
                title="10 boutiques to visit in Mumbai",
            ),
            _evidence(
                title="10 boutiques to visit in Mumbai",
                domain="vogue.in",
                headings=["KALKI Fashion", "Aza Fashions"],
                links=[
                    _link("https://kalkifashion.com/", "KALKI Fashion"),
                    _link("https://azafashions.com/", "Aza Fashions"),
                ],
            ),
            BusinessSignals(),
        )
        names = {row.business_name for row in rows}
        self.assertEqual(names, {"KALKI Fashion", "Aza Fashions"})
        self.assertTrue(all(row.source_type == "ARTICLE" for row in rows))
        self.assertFalse(any("vogue" in (row.business_name or "").lower() for row in rows))

    def test_social_profile_name(self) -> None:
        rows = identify_business_candidates(
            _candidate(
                result_type=ResultType.SOCIAL,
                title="Flaaunt It | Womens Wear Boutique (@flauntitboutiqueindia)",
                url="https://instagram.com/flauntitboutiqueindia",
                normalized_url="https://instagram.com/flauntitboutiqueindia",
                domain="instagram.com",
            ),
            _evidence(
                title="Flaaunt It | Womens Wear Boutique (@flauntitboutiqueindia)",
                domain="instagram.com",
            ),
            BusinessSignals(social_urls=["https://instagram.com/flauntitboutiqueindia"]),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].business_name, "Flaaunt It")
        self.assertEqual(rows[0].instagram, "https://instagram.com/flauntitboutiqueindia")
        self.assertEqual(rows[0].business_type, BusinessType.UNKNOWN)
        self.assertEqual(rows[0].source_type, "SOCIAL")

    def test_instagram_reel_is_not_a_business(self) -> None:
        rows = identify_business_candidates(
            _candidate(
                result_type=ResultType.SOCIAL,
                title="Found Goa's most budget boutique",
                url="https://instagram.com/reel/DROk1BME4m-",
                normalized_url="https://instagram.com/reel/DROk1BME4m-",
                domain="instagram.com",
            ),
            _evidence(
                title="Found Goa's most budget boutique",
                domain="instagram.com",
                source_url="https://instagram.com/reel/DROk1BME4m-",
                final_url="https://instagram.com/reel/DROk1BME4m-",
            ),
            BusinessSignals(),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].business_name, UNKNOWN)
        self.assertIn("social_post", rows[0].evidence["signals"])

    def test_listicle_does_not_use_publisher_as_the_business(self) -> None:
        rows = identify_business_candidates(
            _candidate(
                title="5 Cool Boutiques To Visit for Shopping in Goa",
                url="https://blog.example/5-cool-boutiques-visit-for-shopping-in-goa",
                normalized_url="https://blog.example/5-cool-boutiques-visit-for-shopping-in-goa",
                domain="blog.example",
            ),
            _evidence(
                title="5 Cool Boutiques To Visit for Shopping in Goa",
                source_url="https://blog.example/5-cool-boutiques-visit-for-shopping-in-goa",
                final_url="https://blog.example/5-cool-boutiques-visit-for-shopping-in-goa",
                domain="blog.example",
                text="A shopping guide to boutiques in Goa.",
                links=[_link("https://shop.example/", "Example Boutique")],
            ),
            BusinessSignals(),
        )
        names = {row.business_name for row in rows}
        self.assertIn("Example Boutique", names)
        self.assertNotIn("blog.example", " ".join(names).lower())

    def test_video_explicit_business_name(self) -> None:
        rows = identify_business_candidates(
            _candidate(
                result_type=ResultType.VIDEO,
                title="Shopping at Rozina Boutique",
                url="https://youtube.com/watch?v=abc",
                normalized_url="https://youtube.com/watch?v=abc",
                domain="youtube.com",
            ),
            _evidence(
                title="Shopping at Rozina Boutique",
                meta_description="A visit at Rozina Boutique in Bandra.",
            ),
            BusinessSignals(),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].business_name, "Rozina Boutique")
        self.assertEqual(rows[0].source_type, "VIDEO")
        self.assertEqual(rows[0].confidence, Confidence.LOW)

    def test_contact_information_propagation(self) -> None:
        rows = identify_business_candidates(
            _candidate(title="Rozina"),
            _evidence(
                title="Rozina",
                text="Women's clothing boutique. Visit us.",
            ),
            BusinessSignals(
                emails=["hello@rozina.example", "press@rozina.example"],
                phones=["+91 98765 43210", "022 1234 5678"],
                social_urls=[
                    "https://instagram.com/rozina",
                    "https://facebook.com/rozina",
                ],
                whatsapp_urls=["https://wa.me/919876543210"],
                address_candidates=["12 Linking Road, Mumbai 400050"],
            ),
        )
        row = rows[0]
        self.assertEqual(row.email, "hello@rozina.example")
        self.assertEqual(row.extra_emails, ["press@rozina.example"])
        self.assertEqual(row.phone, "+91 98765 43210")
        self.assertEqual(row.instagram, "https://instagram.com/rozina")
        self.assertEqual(row.facebook, "https://facebook.com/rozina")
        self.assertEqual(row.whatsapp, "https://wa.me/919876543210")

    def test_deterministic_confidence_high(self) -> None:
        rows = identify_business_candidates(
            _candidate(title="Rozina"),
            _evidence(
                title="Rozina",
                text="Women's clothing boutique. Visit us at the showroom.",
            ),
            BusinessSignals(
                emails=["hello@rozina.example"],
                address_candidates=["12 Linking Road, Mumbai 400050"],
            ),
        )
        self.assertEqual(rows[0].confidence, Confidence.HIGH)

    def test_weak_fashion_keyword_stays_unknown(self) -> None:
        rows = identify_business_candidates(
            _candidate(title="City Notes"),
            _evidence(
                title="City Notes",
                text="This article mentions fashion once in passing.",
            ),
            BusinessSignals(),
        )
        self.assertEqual(rows[0].business_type, BusinessType.UNKNOWN)
        self.assertEqual(rows[0].fashion_relevance, Relevance.UNKNOWN)

    def test_in_memory_duplicate_website(self) -> None:
        rows = identify_business_candidates(
            _candidate(
                result_type=ResultType.DIRECTORY,
                url="https://lbb.in/list",
                normalized_url="https://lbb.in/list",
                domain="lbb.in",
            ),
            _evidence(
                domain="lbb.in",
                final_url="https://lbb.in/list",
                links=[
                    _link("https://www.kalkifashion.com/", "KALKI Fashion"),
                    _link("https://kalkifashion.com/about", "KALKI"),
                ],
            ),
            BusinessSignals(),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].business_name, "KALKI Fashion")
        self.assertIn("merged_source_urls", rows[0].evidence)

    def test_duplicate_merge_preserves_contacts_and_evidence(self) -> None:
        left = identify_business_candidates(
            _candidate(title="Rozina"),
            _evidence(title="Rozina"),
            BusinessSignals(
                phones=["1111111111"],
                emails=["first@rozina.example"],
            ),
        )[0]
        right = identify_business_candidates(
            _candidate(title="Rozina"),
            _evidence(title="Rozina", source_url="https://example.com/contact"),
            BusinessSignals(
                phones=["2222222222"],
                emails=["second@rozina.example"],
            ),
        )[0]
        right.source_url = "https://example.com/contact"
        merged = merge_in_memory_duplicates([left, right])[0]
        self.assertEqual(merged.phone, "1111111111")
        self.assertIn("2222222222", merged.extra_phones)
        self.assertIn("second@rozina.example", merged.extra_emails)
        self.assertEqual(
            merged.evidence["merged_source_urls"],
            ["https://example.com/contact"],
        )
        self.assertEqual(len(merged.evidence["merged_evidence"]), 1)

    def test_in_memory_duplicates_can_match_by_phone(self) -> None:
        first = identify_business_candidates(
            _candidate(title="First Boutique"),
            _evidence(title="First Boutique"),
            BusinessSignals(phones=["+91 98765 43210"]),
        )[0]
        second = identify_business_candidates(
            _candidate(title="Second Boutique", url="https://second.example"),
            _evidence(
                title="Second Boutique",
                source_url="https://second.example",
                final_url="https://second.example",
                domain="second.example",
            ),
            BusinessSignals(phones=["919876543210"]),
        )[0]
        first.website = None
        second.website = None
        merged = merge_in_memory_duplicates([first, second])
        self.assertEqual(len(merged), 1)

    def test_provenance_preserved(self) -> None:
        rows = identify_business_candidates(
            _candidate(
                result_type=ResultType.DIRECTORY,
                url="https://wanderlog.com/list/mumbai",
                normalized_url="https://wanderlog.com/list/mumbai",
                domain="wanderlog.com",
            ),
            _evidence(
                domain="wanderlog.com",
                final_url="https://wanderlog.com/list/mumbai",
                links=[_link("https://shop.example/", "Sample Shop")],
            ),
            BusinessSignals(),
        )
        evidence = rows[0].evidence
        self.assertEqual(evidence["source_type"], "DIRECTORY")
        self.assertEqual(evidence["source_url"], "https://wanderlog.com/list/mumbai")
        self.assertEqual(evidence["name_source"], "anchor_text")
        self.assertIn("external_business_link", evidence["signals"])

    def test_phone_alone_is_not_physical_store(self) -> None:
        rows = identify_business_candidates(
            _candidate(title="Rozina"),
            _evidence(title="Rozina", text="Call us anytime. Fashion boutique."),
            BusinessSignals(phones=["+91 98765 43210"]),
        )
        self.assertEqual(rows[0].physical_store, PhysicalStore.UNKNOWN)

    def test_city_is_not_taken_from_a_different_address(self) -> None:
        row = identify_business_candidates(
            _candidate(title="Rozina"),
            _evidence(
                title="Rozina",
                text="Visit our stores. Women's clothing boutique.",
            ),
            BusinessSignals(
                address_candidates=[
                    "Address: 125 Ledbury Road, London W11 2AQ",
                    "12 Linking Road, Mumbai 400050",
                ],
            ),
        )[0]
        self.assertEqual(row.address, "Address: 125 Ledbury Road, London W11 2AQ")
        self.assertEqual(row.city, UNKNOWN)


if __name__ == "__main__":
    unittest.main()
