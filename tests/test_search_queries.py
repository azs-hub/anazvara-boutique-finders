"""Query-generation strategy: families, vocabulary, locations, and bounds."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from candidates import candidates_from_search_results
from geography import ExpectedPlace, search_locations
from search_provider import SearchResult
from search_queries import (
    FAMILIES,
    MAX_QUERIES_PER_PLACE,
    PRODUCTION_FAMILIES,
    SAMPLED_FAMILIES,
    production_queries,
    queries_for_place,
    sample_batch,
    sampled_discovery_queries,
)

# Reference names from external search benchmarks. They must never be queried.
REFERENCE_NAMES = (
    "villa mor",
    "sosa",
    "flame store",
    "savio jon",
    "label zuka",
    "dadablui",
    "people tree",
    "bunti shop",
    "sacha",
    "nana ki",
    "paper boat",
    "no nasties",
    "artjuna",
    "republic of mode",
    "fabric fair",
    "siroi",
    "yellow house",
    "como designer",
    "rangeela",
    "noun goa",
)


def _goa() -> ExpectedPlace:
    return ExpectedPlace(city="Goa", state="Goa", country="India")


def _queries(specs: list[dict]) -> list[str]:
    return [item["query"] for item in specs]


class SearchQueryStrategyTests(unittest.TestCase):
    def test_multiple_search_families_exist(self) -> None:
        self.assertEqual(
            set(FAMILIES),
            {
                "retail_identity",
                "positioning",
                "discovery_language",
                "physical_retail",
                "editorial",
            },
        )
        self.assertEqual(
            PRODUCTION_FAMILIES,
            ("retail_identity", "positioning", "discovery_language"),
        )
        self.assertEqual(SAMPLED_FAMILIES, ("physical_retail", "editorial"))
        for name, templates in FAMILIES.items():
            self.assertGreaterEqual(len(templates), 5, name)

    def test_vocabulary_is_broader_than_boutique_only(self) -> None:
        specs = production_queries(_goa())
        blob = " ".join(query.casefold() for query in _queries(specs))
        self.assertTrue(any("boutique" not in query.casefold() for query in _queries(specs)))
        for term in (
            "shop",
            "shops",
            "store",
            "collective",
            "concept",
            "curated",
            "showroom",
            "destination",
        ):
            self.assertIn(term, blob)

    def test_required_recall_terms_are_present(self) -> None:
        blob = " ".join(query.casefold() for query in _queries(production_queries(_goa())))
        for term in (
            "independent",
            "indian designers",
            "linen",
            "village shops",
            "fashion collective",
        ):
            self.assertIn(term, blob)

    def test_location_is_preserved_for_any_city(self) -> None:
        goa = production_queries(_goa())
        self.assertTrue(all(item["city"] == "Goa" for item in goa))
        self.assertTrue(all(item["state"] == "Goa" and item["country"] == "India" for item in goa))
        self.assertTrue(any(item["location"] == "Goa" for item in goa))
        localities = [item for item in goa if item["location"].casefold() != "goa"]
        self.assertTrue(localities)
        self.assertIn("Assagao", {item["location"] for item in localities})
        self.assertIn("Panaji", {item["location"] for item in localities})
        self.assertTrue(all("Goa" not in item["query"] or item["city"] == "Goa" for item in goa))
        self.assertTrue(any(item["query"].endswith("Assagao") for item in localities))

        mumbai = production_queries(ExpectedPlace(city="Mumbai", state="Maharashtra", country="India"))
        self.assertTrue(all(item["city"] == "Mumbai" for item in mumbai))
        self.assertTrue(any(item["location"] == "Bandra" for item in mumbai))
        self.assertFalse(any(item["query"].casefold().endswith(" goa") for item in mumbai))

        lisbon = ExpectedPlace(city="Lisbon", state="", country="Portugal")
        generic = production_queries(lisbon)
        self.assertEqual(search_locations(lisbon), ("Lisbon",))
        self.assertTrue(all(item["query"].endswith("Lisbon") for item in generic))
        self.assertTrue(all(item["city"] == "Lisbon" for item in generic))

    def test_query_generation_stays_bounded(self) -> None:
        place = _goa()
        specs = production_queries(place)
        locations = search_locations(place)
        template_count = sum(len(FAMILIES[name]) for name in PRODUCTION_FAMILIES)
        cartesian = template_count * len(locations)
        self.assertGreater(cartesian, len(specs))
        self.assertLessEqual(len(specs), template_count + (len(locations) - 1))
        self.assertLessEqual(len(specs), MAX_QUERIES_PER_PLACE)
        self.assertGreater(len(specs), template_count)

        everything = queries_for_place(place, families=tuple(FAMILIES))
        all_templates = sum(len(templates) for templates in FAMILIES.values())
        self.assertLess(len(everything), all_templates * len(locations))
        self.assertLessEqual(len(everything), MAX_QUERIES_PER_PLACE)

        sampled = sampled_discovery_queries(place)
        self.assertTrue({item["family"] for item in sampled} <= set(SAMPLED_FAMILIES))
        self.assertLess(len(sampled), len(specs))

        small = sample_batch()
        self.assertEqual(len(small), 5 * len(FAMILIES))
        self.assertLess(len(small), len(specs))

    def test_reference_businesses_are_not_in_production_queries(self) -> None:
        blob = " ".join(
            item["query"].casefold()
            for item in (
                *production_queries(_goa()),
                *sampled_discovery_queries(_goa()),
                *sample_batch(),
            )
        )
        for name in REFERENCE_NAMES:
            self.assertNotIn(name, blob)

    def test_discovery_keeps_hits_without_retail_keywords(self) -> None:
        result = SearchResult(
            title="Lane House",
            url="https://lane-house.example/",
            snippet="A village shop beside the church.",
            source="searxng",
        )
        candidates = candidates_from_search_results(
            [result],
            "village shops fashion Goa",
        )
        self.assertEqual(len(candidates), 1)
        haystack = f"{candidates[0].title} {candidates[0].snippet}".casefold()
        for token in ("boutique", "store", "fashion", "designer", "multi-brand", "multi brand"):
            self.assertNotIn(token, haystack)


if __name__ == "__main__":
    unittest.main()
