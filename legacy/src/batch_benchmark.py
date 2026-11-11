"""Multi-query batch orchestration for the existing LLM benchmark.

Collects search candidates per query, globally deduplicates with the
existing candidate keys, then runs ``evaluate_candidates`` once.
Does not change discovery, fetch, enrichment, Qwen, or classification.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from benchmark import OUTPUT_DIR, candidate_to_dict, collect_search_results
from candidates import Candidate, candidates_from_search_results, dedupe_candidates, discovery_queries
from geography import resolve_expected_place
from llm_benchmark import evaluate_candidates
from searxng_provider import SearXNGSearchProvider

SearchFn = Callable[[str, int], tuple[list, str | None]]
EvaluateFn = Callable[..., dict]


def load_batch_queries(path: Path | str) -> list[dict[str, Any]]:
    """Load a JSON array of ``{"query": "...", "limit": N}`` objects."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("Batch file must be a non-empty JSON array")
    specs: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Entry {index} must be an object with a query")
        query = str(item.get("query") or "").strip()
        if not query:
            raise ValueError(f"Entry {index} needs a non-empty query")
        limit = int(item.get("limit", 20))
        if limit < 1:
            raise ValueError(f"Entry {index} limit must be at least 1")
        specs.append(
            {
                "query": query,
                "limit": limit,
                "family": str(item.get("family") or ""),
                "city": str(item.get("city") or "").strip(),
                "state": str(item.get("state") or "").strip(),
                "country": str(item.get("country") or "").strip(),
            }
        )
    return specs


def _live_search_fn() -> SearchFn:
    provider = SearXNGSearchProvider.from_env()
    if provider is None:
        raise RuntimeError("SEARXNG_URL is not set. Copy .env.example to .env.")

    def search(query: str, limit: int) -> tuple[list, str | None]:
        return collect_search_results(provider, query, limit)

    return search


def collect_batch_candidates(
    specs: list[dict[str, Any]],
    *,
    search_fn: SearchFn | None = None,
    progress: Callable[[str], None] = print,
) -> tuple[list[Candidate], list[dict[str, Any]], list[dict], int]:
    """Search each query independently. One failure does not stop the batch."""
    search = search_fn or _live_search_fn()
    collected: list[Candidate] = []
    query_metrics: list[dict[str, Any]] = []
    errors: list[dict] = []
    raw_total = 0
    total = len(specs)
    for index, spec in enumerate(specs, start=1):
        query = spec["query"]
        limit = int(spec["limit"])
        progress(f"[BATCH] Query {index}/{total}: {query}")
        progress(f"[BATCH] Requested: {limit}")
        raw: list = []
        error: str | None = None
        try:
            raw, error = search(query, limit)
        except Exception as exc:
            error = str(exc)
            raw = []
        if error and not raw:
            errors.append({"stage": "search", "query": query, "error": error})
            progress(f"[BATCH] FAILED: {error}")
            query_metrics.append(
                {
                    "query": query,
                    "requested_limit": limit,
                    "raw_results": 0,
                    "candidates": 0,
                    "error": error,
                }
            )
            progress("")
            continue
        if error:
            errors.append({"stage": "search", "query": query, "error": error})
        candidates = candidates_from_search_results(raw, query)[:limit]
        place = resolve_expected_place(
            city=spec.get("city") or None,
            state=spec.get("state") or None,
            country=spec.get("country") or None,
            query=query,
        )
        if place is not None:
            candidates = [
                replace(
                    item,
                    expected_city=place.city,
                    expected_state=place.state,
                    expected_country=place.country,
                )
                for item in candidates
            ]
        raw_total += len(raw)
        collected.extend(candidates)
        progress(f"[BATCH] Retrieved: {len(candidates)}")
        progress("")
        query_metrics.append(
            {
                "query": query,
                "requested_limit": limit,
                "raw_results": len(raw),
                "candidates": len(candidates),
                "error": error,
            }
        )
    return collected, query_metrics, errors, raw_total


def finalize_query_metrics(
    partial: list[dict[str, Any]],
    unique_candidates: list[Candidate],
    row_dicts: list[dict],
) -> list[dict[str, Any]]:
    """Add post-dedup / post-pipeline counts the existing report can support."""
    finalized = []
    for metric in partial:
        query = metric["query"]
        unique = sum(1 for item in unique_candidates if query in discovery_queries(item))
        query_rows = [
            row
            for row in row_dicts
            if query in list((row.get("evidence") or {}).get("discovered_by_queries") or [])
        ]
        finalized.append(
            {
                **metric,
                "unique_after_global_dedup": unique,
                "businesses": sum(1 for row in query_rows if _row_is_valid_business(row)),
                "stockist_leads": sum(
                    1
                    for row in query_rows
                    if str((row.get("evidence") or {}).get("stockist_lead") or "").upper()
                    == "YES"
                ),
            }
        )
    return finalized


def _row_is_valid_business(row: dict) -> bool:
    if row.get("source_type") in {"DIRECTORY", "ARTICLE", "SOCIAL", "VIDEO"}:
        return False
    name = row.get("business_name")
    business_type = row.get("business_type")
    return bool(name) and name != "UNKNOWN" and business_type not in {None, "UNKNOWN"}


def assemble_batch_report(
    *,
    specs: list[dict[str, Any]],
    pipeline: dict,
    unique_candidates: list[Candidate],
    before_dedup: int,
    raw_search_results: int,
    query_metrics: list[dict[str, Any]],
) -> dict:
    ident = (pipeline.get("counts") or {}).get("B_rules_plus_qwen") or {}
    stockist = pipeline.get("stockist") or {}
    after_ai = stockist.get("after_ai") or {}
    leads = stockist.get("leads") or {}
    llm = pipeline.get("llm") or {}
    report = dict(pipeline)
    report["benchmark_type"] = "multi_query_batch"
    report["queries"] = specs
    report["batch_summary"] = {
        "requested_candidates": sum(int(spec["limit"]) for spec in specs),
        "raw_search_results": raw_search_results,
        "candidates_before_global_dedup": before_dedup,
        "unique_candidates_after_global_dedup": len(unique_candidates),
        "valid_business_entities": ident.get("valid_business_entities", 0),
        "stockist_leads_yes": leads.get("YES", 0),
        "potential_stockist_yes": after_ai.get("YES", 0),
        "qwen_calls": llm.get("qwen_calls", 0),
        "runtime_seconds": pipeline.get("runtime_seconds", 0),
    }
    report["query_metrics"] = query_metrics
    report["candidates"] = [candidate_to_dict(item) for item in unique_candidates]
    return report


def run_batch_benchmark(
    specs: list[dict[str, Any]],
    *,
    enable_llm: bool,
    search_fn: SearchFn | None = None,
    evaluate_fn: EvaluateFn | None = None,
    progress: Callable[[str], None] = print,
) -> dict:
    started = time.monotonic()
    collected, partial_metrics, errors, raw_total = collect_batch_candidates(
        specs,
        search_fn=search_fn,
        progress=progress,
    )
    unique = dedupe_candidates(collected)
    progress(f"[BATCH] Total candidates before global dedup: {len(collected)}")
    progress(f"[BATCH] Unique candidates after global dedup: {len(unique)}")
    progress("[BATCH] Starting existing processing pipeline...")
    evaluate = evaluate_fn or evaluate_candidates
    pipeline = evaluate(
        unique,
        enable_llm=enable_llm,
        query_label="multi_query_batch",
        extra_errors=errors,
        raw_search_count=raw_total,
        limit=sum(int(spec["limit"]) for spec in specs),
        started=started,
    )
    query_metrics = finalize_query_metrics(
        partial_metrics,
        unique,
        pipeline.get("business_candidates_llm") or [],
    )
    return assemble_batch_report(
        specs=specs,
        pipeline=pipeline,
        unique_candidates=unique,
        before_dedup=len(collected),
        raw_search_results=raw_total,
        query_metrics=query_metrics,
    )


def write_batch_json(report: dict, *, output_dir: Path | None = None) -> Path:
    dest = Path(output_dir or OUTPUT_DIR)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = dest / f"benchmark_batch_{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def run_batch_main(args) -> int:
    path = Path(args.batch)
    if not path.is_file():
        print(f"Batch file not found: {path}", file=sys.stderr)
        return 1
    try:
        specs = load_batch_queries(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Invalid batch file: {exc}", file=sys.stderr)
        return 1
    try:
        report = run_batch_benchmark(specs, enable_llm=not args.no_llm)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    output = "(skipped)"
    if not args.no_json:
        output = str(write_batch_json(report))
    print()
    print("[BATCH] Complete")
    print(f"[BATCH] Output: {output}")
    print(f"[BATCH] Runtime: {report['batch_summary']['runtime_seconds']}s")
    return 0
