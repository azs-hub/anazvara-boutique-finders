"""Offline tests for multi-query batch benchmark orchestration."""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from batch_benchmark import (
    assemble_batch_report,
    collect_batch_candidates,
    finalize_query_metrics,
    load_batch_queries,
    run_batch_benchmark,
    write_batch_json,
)
from candidates import Candidate, dedupe_candidates, discovery_queries
from classification import ResultType
from llm_benchmark import build_parser, evaluate_candidates, main
from search_provider import SearchResult


QUERY_A = "women's fashion boutique Goa"
QUERY_B = "multi brand fashion store Goa"
QUERY_C = "concept store Goa fashion"


def _hit(url: str, title: str = "Shop") -> SearchResult:
    return SearchResult(title=title, url=url, snippet="", source="test")


def _pipeline(candidates: list[Candidate], **kwargs) -> dict:
    rows = []
    for item in candidates:
        rows.append(
            {
                "business_name": item.title or "Shop",
                "business_type": "BOUTIQUE",
                "source_type": "WEBSITE",
                "evidence": {
                    "discovered_by_queries": list(discovery_queries(item)),
                    "discovery_query": item.search_query,
                    "stockist_lead": "YES",
                    "ai_potential_stockist": "UNKNOWN",
                    "rule_potential_stockist": "UNKNOWN",
                },
            }
        )
    return {
        "runtime_seconds": 0.2,
        "llm": {"qwen_calls": len(candidates)},
        "stockist": {
            "after_ai": {"YES": 0, "NO": 0, "UNKNOWN": len(candidates)},
            "leads": {"YES": len(candidates), "NO": 0, "UNKNOWN": 0},
        },
        "counts": {"B_rules_plus_qwen": {"valid_business_entities": len(candidates)}},
        "business_candidates_llm": rows,
        "errors": list(kwargs.get("extra_errors") or []),
    }


class BatchQueryFileTests(unittest.TestCase):
    def test_loads_example_queries_file(self) -> None:
        path = Path(__file__).resolve().parent.parent / "queries_1000.json"
        specs = load_batch_queries(path)
        self.assertEqual(len(specs), 8)
        self.assertEqual(specs[0]["query"], QUERY_A)
        self.assertEqual(specs[0]["limit"], 150)
        self.assertEqual(sum(item["limit"] for item in specs), 1000)

    def test_rejects_empty_or_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_batch_queries(bad)
            bad.write_text('{"query": "x"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_batch_queries(bad)


class BatchCliTests(unittest.TestCase):
    def test_single_query_cli_unchanged(self) -> None:
        args = build_parser().parse_args(
            ["women's fashion boutique Goa", "--limit", "20"]
        )
        self.assertIsNone(args.batch)
        self.assertEqual(" ".join(args.query), "women's fashion boutique Goa")
        self.assertEqual(args.limit, 20)
        self.assertFalse(args.no_llm)

    def test_batch_flag_parses(self) -> None:
        args = build_parser().parse_args(["--batch", "queries_1000.json"])
        self.assertEqual(args.batch, "queries_1000.json")

    def test_single_query_main_still_dispatches(self) -> None:
        with patch("llm_benchmark.run_llm_benchmark") as mock_run:
            mock_run.return_value = {}
            with patch("llm_benchmark.print_summary"):
                code = main(
                    ["women's fashion boutique Goa", "--limit", "20", "--no-json"]
                )
        self.assertEqual(code, 0)
        mock_run.assert_called_once_with(
            "women's fashion boutique Goa",
            20,
            enable_llm=True,
        )


class BatchOrchestrationTests(unittest.TestCase):
    def test_multiple_queries_are_executed_and_combined(self) -> None:
        calls: list[str] = []

        def search(query: str, limit: int):
            calls.append(query)
            if query == QUERY_A:
                return [_hit("https://rangeela.example/", "Rangeela")], None
            if query == QUERY_B:
                return [_hit("https://sosa.example/", "Sosa")], None
            return [], "no results"

        collected, metrics, errors, raw_total = collect_batch_candidates(
            [
                {"query": QUERY_A, "limit": 2},
                {"query": QUERY_B, "limit": 2},
                {"query": QUERY_C, "limit": 2},
            ],
            search_fn=search,
            progress=lambda _line: None,
        )
        self.assertEqual(calls, [QUERY_A, QUERY_B, QUERY_C])
        self.assertEqual(len(collected), 2)
        self.assertEqual({item.domain for item in collected}, {"rangeela.example", "sosa.example"})
        self.assertEqual(raw_total, 2)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["query"], QUERY_C)
        self.assertEqual(metrics[2]["error"], "no results")

    def test_failed_query_does_not_abort_batch(self) -> None:
        def search(query: str, limit: int):
            if query == QUERY_B:
                raise RuntimeError("searxng timeout")
            return [_hit(f"https://{query.split()[-1].lower()}.example/")], None

        specs = [
            {"query": QUERY_A, "limit": 1},
            {"query": QUERY_B, "limit": 1},
            {"query": QUERY_C, "limit": 1},
        ]
        report = run_batch_benchmark(
            specs,
            enable_llm=False,
            search_fn=search,
            evaluate_fn=_pipeline,
            progress=lambda _line: None,
        )
        self.assertEqual(report["benchmark_type"], "multi_query_batch")
        self.assertEqual(len(report["candidates"]), 2)
        failed = next(item for item in report["query_metrics"] if item["query"] == QUERY_B)
        self.assertEqual(failed["error"], "searxng timeout")
        self.assertEqual(failed["candidates"], 0)
        self.assertTrue(any(item.get("query") == QUERY_B for item in report["errors"]))

    def test_global_dedup_and_multi_query_provenance(self) -> None:
        shared = "https://rangeela.example/"

        def search(query: str, limit: int):
            if query == QUERY_A:
                return [_hit(shared, "Rangeela"), _hit("https://other.example/")], None
            return [_hit(shared, "Rangeela Goa")], None

        report = run_batch_benchmark(
            [{"query": QUERY_A, "limit": 5}, {"query": QUERY_B, "limit": 5}],
            enable_llm=True,
            search_fn=search,
            evaluate_fn=_pipeline,
            progress=lambda _line: None,
        )
        self.assertEqual(report["batch_summary"]["candidates_before_global_dedup"], 3)
        self.assertEqual(report["batch_summary"]["unique_candidates_after_global_dedup"], 2)
        shared_row = next(
            item for item in report["candidates"] if item["domain"] == "rangeela.example"
        )
        self.assertEqual(
            set(shared_row["discovered_by_queries"]),
            {QUERY_A, QUERY_B},
        )
        self.assertEqual(shared_row["discovery_query"], QUERY_A)
        metrics = {item["query"]: item for item in report["query_metrics"]}
        self.assertEqual(metrics[QUERY_A]["unique_after_global_dedup"], 2)
        self.assertEqual(metrics[QUERY_B]["unique_after_global_dedup"], 1)

    def test_exactly_one_batch_output_json(self) -> None:
        def search(query: str, limit: int):
            return [_hit("https://one.example/")], None

        report = run_batch_benchmark(
            [{"query": QUERY_A, "limit": 1}],
            enable_llm=False,
            search_fn=search,
            evaluate_fn=_pipeline,
            progress=lambda _line: None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            path = write_batch_json(report, output_dir=dest)
            written = list(dest.glob("benchmark_batch_*.json"))
            self.assertEqual(len(written), 1)
            self.assertEqual(written[0], path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["benchmark_type"], "multi_query_batch")
            self.assertIn("batch_summary", payload)
            self.assertIn("query_metrics", payload)
            self.assertIn("candidates", payload)

    def test_qwen_not_called_twice_for_same_deduped_candidate(self) -> None:
        seen: list[int] = []

        def search(query: str, limit: int):
            return [_hit("https://shared.example/")], None

        def evaluate(candidates, **kwargs):
            seen.append(len(candidates))
            return _pipeline(candidates, **kwargs)

        report = run_batch_benchmark(
            [{"query": QUERY_A, "limit": 3}, {"query": QUERY_B, "limit": 3}],
            enable_llm=True,
            search_fn=search,
            evaluate_fn=evaluate,
            progress=lambda _line: None,
        )
        self.assertEqual(seen, [1])
        self.assertEqual(report["batch_summary"]["qwen_calls"], 1)

    def test_default_pipeline_is_existing_evaluate_candidates(self) -> None:
        with patch("batch_benchmark.evaluate_candidates") as mock_eval:
            mock_eval.return_value = _pipeline([])
            run_batch_benchmark(
                [{"query": QUERY_A, "limit": 1}],
                enable_llm=True,
                search_fn=lambda query, limit: ([_hit("https://x.example/")], None),
                progress=lambda _line: None,
            )
            mock_eval.assert_called_once()
            passed = mock_eval.call_args[0][0]
            self.assertEqual(len(passed), 1)

    def test_existing_classification_fields_are_not_rewritten(self) -> None:
        pipeline = {
            "runtime_seconds": 1,
            "llm": {"qwen_calls": 2},
            "stockist": {
                "after_ai": {"YES": 1, "NO": 0, "UNKNOWN": 1},
                "leads": {"YES": 1, "NO": 0, "UNKNOWN": 1},
            },
            "counts": {"B_rules_plus_qwen": {"valid_business_entities": 1}},
            "business_candidates_llm": [
                {
                    "business_name": "Rangeela",
                    "business_type": "CONCEPT_STORE",
                    "source_type": "WEBSITE",
                    "evidence": {
                        "ai_potential_stockist": "NO",
                        "stockist_lead": "YES",
                        "discovered_by_queries": [QUERY_A],
                    },
                }
            ],
        }
        report = assemble_batch_report(
            specs=[{"query": QUERY_A, "limit": 1}],
            pipeline=pipeline,
            unique_candidates=[],
            before_dedup=0,
            raw_search_results=0,
            query_metrics=[],
        )
        self.assertEqual(
            report["business_candidates_llm"][0]["evidence"]["ai_potential_stockist"],
            "NO",
        )
        self.assertEqual(
            report["business_candidates_llm"][0]["evidence"]["stockist_lead"],
            "YES",
        )
        self.assertEqual(report["batch_summary"]["potential_stockist_yes"], 1)
        self.assertEqual(report["batch_summary"]["stockist_leads_yes"], 1)

    def test_existing_pipeline_still_runs_stockist_and_qwen_hooks(self) -> None:
        source = inspect.getsource(evaluate_candidates)
        self.assertIn("attach_stockist_lead", source)
        self.assertIn("maybe_classify_with_local_llm", source)
        self.assertIn("identify_business_candidates", source)

    def test_dedupe_reuses_existing_website_domain_key(self) -> None:
        first = Candidate(
            title="About",
            url="https://shop.example/about",
            normalized_url="https://shop.example/about",
            domain="shop.example",
            snippet="",
            result_type=ResultType.WEBSITE,
            search_query=QUERY_A,
            search_source="test",
            discovered_by_queries=(QUERY_A,),
        )
        second = Candidate(
            title="Home",
            url="https://shop.example/",
            normalized_url="https://shop.example",
            domain="shop.example",
            snippet="",
            result_type=ResultType.WEBSITE,
            search_query=QUERY_B,
            search_source="test",
            discovered_by_queries=(QUERY_B,),
        )
        merged = dedupe_candidates([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].normalized_url, "https://shop.example")
        self.assertEqual(set(discovery_queries(merged[0])), {QUERY_A, QUERY_B})


class BatchMetricsTests(unittest.TestCase):
    def test_finalize_query_metrics_counts_provenance(self) -> None:
        candidate = Candidate(
            title="Shop",
            url="https://shop.example/",
            normalized_url="https://shop.example",
            domain="shop.example",
            snippet="",
            result_type=ResultType.WEBSITE,
            search_query=QUERY_A,
            search_source="test",
            discovered_by_queries=(QUERY_A, QUERY_B),
        )
        metrics = finalize_query_metrics(
            [
                {"query": QUERY_A, "requested_limit": 2, "raw_results": 2, "candidates": 1},
                {"query": QUERY_B, "requested_limit": 2, "raw_results": 1, "candidates": 1},
            ],
            [candidate],
            [
                {
                    "business_name": "Shop",
                    "business_type": "BOUTIQUE",
                    "source_type": "WEBSITE",
                    "evidence": {
                        "discovered_by_queries": [QUERY_A, QUERY_B],
                        "stockist_lead": "YES",
                    },
                }
            ],
        )
        by_query = {item["query"]: item for item in metrics}
        self.assertEqual(by_query[QUERY_A]["unique_after_global_dedup"], 1)
        self.assertEqual(by_query[QUERY_B]["unique_after_global_dedup"], 1)
        self.assertEqual(by_query[QUERY_A]["stockist_leads"], 1)
        self.assertEqual(by_query[QUERY_B]["businesses"], 1)


if __name__ == "__main__":
    unittest.main()
