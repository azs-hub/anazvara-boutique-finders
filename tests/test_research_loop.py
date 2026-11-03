"""Offline tests for the bounded iterative research loop."""

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
from candidates import expand_from_page
from content_extraction import PageEvidence
from geography import ExpectedPlace
from research_loop import (
    INITIAL_DISCOVERY_MAX_QUERIES,
    CANDIDATE_ENRICHMENT_MAX_QUERIES,
    EXPANSION_MAX_QUERIES,
    TOTAL_RESEARCH_MAX_QUERIES,
    ResearchBudget,
    ResearchCandidate,
    dedupe_research_candidates,
    plan_enrichment_queries,
    plan_expansion_queries,
    plan_initial_queries,
    run_research_loop,
)
from search_provider import SearchResult

GOA = ExpectedPlace(city="Goa", state="Goa", country="India")
REFERENCE_NAMES = (
    "villa mor",
    "sosa",
    "flame store",
    "label zuka",
    "rangeela",
    "people tree",
    "paper boat",
    "yellow house",
    "nana ki",
)


def _business(
    name: str,
    *,
    website: str | None = None,
    instagram: str | None = None,
    address: str | None = None,
    snippet: str = "",
    brands_known: bool = False,
    source_url: str = "",
) -> BusinessCandidate:
    evidence = {"snippet": snippet, "discovery_sources": []}
    if brands_known:
        evidence["brands_known"] = True
    return BusinessCandidate(
        business_name=name,
        website=website,
        instagram=instagram,
        facebook=None,
        whatsapp=None,
        phone=None,
        email=None,
        address=address,
        city="Goa",
        source_url=source_url or website or "",
        source_type="WEBSITE",
        business_type=BusinessType.UNKNOWN,
        fashion_relevance=Relevance.UNKNOWN,
        women_fashion_relevance=Relevance.UNKNOWN,
        physical_store=PhysicalStore.UNKNOWN,
        evidence=evidence,
        confidence=Confidence.LOW,
    )


def _row(business: BusinessCandidate, *, round_no: int = 1, research_id: str = "r1") -> ResearchCandidate:
    return ResearchCandidate(
        business=business,
        discovery_round=round_no,
        discovery_source=business.source_url or "query",
        research_id=research_id,
    )


def _hit(title: str, url: str, snippet: str) -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet, source="searxng")


class ResearchLoopTests(unittest.TestCase):
    def test_initial_discovery_produces_candidates(self) -> None:
        def search(query: str, limit: int):
            return [
                _hit(
                    "Coastal Edit",
                    "https://coastal.example/",
                    "Independent fashion boutique in Panaji.",
                )
            ]

        report = run_research_loop(
            GOA,
            search,
            budget=ResearchBudget(
                initial_discovery_max_queries=2,
                candidate_enrichment_max_queries=0,
                expansion_max_queries=0,
                total_research_max_queries=2,
            ),
            now=lambda: "2026-09-28T00:00:00+00:00",
        )
        names = [row.business.business_name for row in report.candidates]
        self.assertIn("Coastal Edit", names)
        self.assertTrue(all(item["round"] == 1 for item in report.queries))
        self.assertLessEqual(len(report.queries), 2)

    def test_enrichment_queries_target_only_missing_fields(self) -> None:
        row = _row(
            _business(
                "Coastal Edit",
                website="https://coastal.example/",
                snippet="Independent fashion boutique in Panaji.",
            )
        )
        specs = plan_enrichment_queries(row, GOA, limit=2, research_round=2)
        purposes = [item["purpose"] for item in specs]
        self.assertEqual(purposes, ["address", "instagram"])
        blob = " ".join(item["query"] for item in specs).casefold()
        self.assertNotIn("website", blob)
        self.assertIn("address", blob)
        self.assertIn("instagram", blob)

    def test_known_fields_are_not_searched_again(self) -> None:
        row = _row(
            _business(
                "Coastal Edit",
                website="https://coastal.example/",
                instagram="https://instagram.com/coastal.edit",
                address="12 Main Road, Panaji 403001",
                snippet="Independent fashion boutique.",
                brands_known=True,
            )
        )
        self.assertEqual(plan_enrichment_queries(row, GOA, limit=6, research_round=2), [])
        wholesale = _row(
            _business("Bulk Textiles", snippet="Wholesale manufacturer. Online-only.")
        )
        self.assertEqual(plan_enrichment_queries(wholesale, GOA, limit=6, research_round=2), [])

    def test_geographic_expansion_targets_uncovered_localities(self) -> None:
        rows = [
            _row(_business("Panaji Edit", snippet="Shop in Panaji."), research_id="a"),
            _row(_business("Assagao Edit", snippet="Shop in Assagao."), research_id="b"),
            _row(_business("Anjuna Edit", snippet="Shop in Anjuna."), research_id="c"),
        ]
        specs = plan_expansion_queries(GOA, rows, limit=8)
        gaps = [item for item in specs if item["purpose"] == "locality_gap"]
        self.assertTrue(gaps)
        locations = {item["location"] for item in gaps}
        self.assertNotIn("Panaji", locations)
        self.assertNotIn("Assagao", locations)
        self.assertNotIn("Anjuna", locations)
        self.assertLessEqual(len(specs), 8)

    def test_editorial_discovery_queries_are_generated(self) -> None:
        specs = plan_expansion_queries(GOA, [], limit=6)
        editorial = [item for item in specs if item["purpose"] == "editorial"]
        self.assertTrue(editorial)
        blob = " ".join(item["query"].casefold() for item in editorial)
        self.assertIn("goa", blob)
        self.assertTrue("guide" in blob or "boutiques to visit" in blob)

    def test_rounds_and_total_budget_are_hard_caps(self) -> None:
        calls: list[str] = []

        def search(query: str, limit: int):
            calls.append(query)
            return [
                _hit(
                    "Coastal Edit",
                    "https://coastal.example/",
                    "Independent fashion boutique in Panaji.",
                )
            ]

        wide = ResearchBudget(
            initial_discovery_max_queries=3,
            candidate_enrichment_max_queries=2,
            expansion_max_queries=2,
            total_research_max_queries=40,
            max_queries_per_candidate=2,
        )
        report = run_research_loop(GOA, search, budget=wide, now=lambda: "2026-09-28T00:00:00+00:00")
        by_round: dict[int, int] = {}
        for item in report.queries:
            by_round[item["round"]] = by_round.get(item["round"], 0) + 1
        self.assertLessEqual(by_round.get(1, 0), 3)
        self.assertLessEqual(by_round.get(2, 0), 2)
        self.assertLessEqual(by_round.get(3, 0), 2)
        self.assertLessEqual(sum(by_round.values()), 40)
        self.assertTrue(set(by_round) <= {1, 2, 3, 4})

        tight_calls: list[str] = []

        def tight_search(query: str, limit: int):
            tight_calls.append(query)
            return search(query, limit)

        tight = ResearchBudget(
            initial_discovery_max_queries=30,
            candidate_enrichment_max_queries=30,
            expansion_max_queries=30,
            total_research_max_queries=7,
        )
        tight_report = run_research_loop(GOA, tight_search, budget=tight)
        self.assertLessEqual(len(tight_calls), 7)
        self.assertLessEqual(len(tight_report.queries), TOTAL_RESEARCH_MAX_QUERIES)
        self.assertLessEqual(len(tight_report.queries), 7)
        self.assertLessEqual(INITIAL_DISCOVERY_MAX_QUERIES, 24)
        self.assertLessEqual(CANDIDATE_ENRICHMENT_MAX_QUERIES, 18)
        self.assertLessEqual(EXPANSION_MAX_QUERIES, 12)

    def test_later_round_candidate_merges_and_keeps_provenance(self) -> None:
        article_url = "https://magazine.example/articles/goa-shops"
        text = "1. Coastal Edit, Panaji 2. Harbour Cloth, Palolem"

        def search(query: str, limit: int):
            lowered = query.casefold()
            if "guide" in lowered or "boutiques to visit" in lowered or "where to shop" in lowered:
                return [_hit("Best independent shops in Goa", article_url, "A shopping guide.")]
            if lowered.startswith('"'):
                return [
                    _hit(
                        "Coastal Edit",
                        "https://coastal.example/visit",
                        "Visit us at 12 Main Road, Panaji 403001.",
                    )
                ]
            return [
                _hit(
                    "Coastal Edit",
                    "https://coastal.example/",
                    "Independent fashion boutique in Panaji.",
                )
            ]

        def expand(candidate):
            page = PageEvidence(
                source_url=candidate.normalized_url,
                final_url=candidate.normalized_url,
                domain=candidate.domain,
                title=candidate.title,
                meta_description=None,
                text=text,
                headings=[],
                links=[],
            )
            return expand_from_page(candidate, page_evidence=page, expected=GOA)

        report = run_research_loop(
            GOA,
            search,
            budget=ResearchBudget(
                initial_discovery_max_queries=2,
                candidate_enrichment_max_queries=6,
                expansion_max_queries=4,
                total_research_max_queries=20,
                max_queries_per_candidate=2,
            ),
            expand_page=expand,
            now=lambda: "2026-09-28T00:00:00+00:00",
        )
        coastal = [row for row in report.candidates if row.business.business_name == "Coastal Edit"]
        self.assertEqual(len(coastal), 1)
        sources = coastal[0].business.evidence.get("discovery_sources") or []
        urls = {item.get("url") for item in sources}
        self.assertIn("https://coastal.example", urls)
        self.assertIn(article_url, urls)
        rounds = {item.get("round") for item in sources}
        self.assertIn(1, rounds)
        self.assertIn(3, rounds)
        self.assertEqual(coastal[0].discovery_round, 1)
        coastal_queries = [item["query"] for item in report.queries if "coastal edit" in item["query"].casefold()]
        self.assertTrue(coastal_queries)
        self.assertFalse(any(query.casefold().endswith(" website") for query in coastal_queries))
        self.assertTrue(coastal[0].research_queries_run)
        self.assertEqual(coastal[0].last_researched_at, "2026-09-28T00:00:00+00:00")

        harbour = [row for row in report.candidates if row.business.business_name == "Harbour Cloth"]
        self.assertEqual(len(harbour), 1)
        self.assertEqual(harbour[0].discovery_round, 3)
        self.assertTrue(
            any(item.get("url") == article_url for item in harbour[0].business.evidence.get("discovery_sources") or [])
        )
        self.assertTrue(
            any("harbour cloth" in item["query"].casefold() for item in report.queries if item["round"] == 4)
        )

        direct = dedupe_research_candidates(
            [
                _row(
                    _business("Coastal Edit", website="https://coastal.example/", source_url="https://coastal.example"),
                    research_id="early",
                ),
                _row(
                    _business("Coastal Edit", source_url=article_url),
                    round_no=3,
                    research_id="later",
                ),
            ]
        )
        self.assertEqual(len(direct), 1)
        self.assertEqual(direct[0].discovery_round, 1)
        self.assertNotEqual(direct[0].business.business_name, UNKNOWN)

    def test_initial_slice_stays_inside_the_existing_strategy(self) -> None:
        specs = plan_initial_queries(GOA, limit=6)
        self.assertLessEqual(len(specs), 6)
        self.assertTrue(all(item["round"] == 1 for item in specs))
        self.assertTrue(all(item["city"] == "Goa" for item in specs))
        families = {item["family"] for item in specs}
        self.assertTrue(families <= {"retail_identity", "positioning", "discovery_language"})

    def test_module_does_not_hardcode_reference_businesses(self) -> None:
        source = (SRC_DIR / "research_loop.py").read_text(encoding="utf-8").casefold()
        for name in REFERENCE_NAMES:
            self.assertNotIn(name, source)


if __name__ == "__main__":
    unittest.main()
