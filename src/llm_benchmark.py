"""Compare rule-only identification against rules + local Qwen.

Usage:
    python src/llm_benchmark.py "women's fashion boutique Mumbai" --limit 20
    python src/llm_benchmark.py "women's fashion boutique Mumbai" --limit 20 --no-llm

Reuses the existing fetch + rule pipeline. Does not replace rule fields.
Path B fills UNKNOWN rule values from validated Qwen output only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from benchmark import (
    OUTPUT_DIR,
    business_to_dict,
    candidate_to_dict,
    collect_search_results,
    _enum_count,
    _has_value,
)
from business_candidates import (
    UNKNOWN,
    BusinessCandidate,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
    identify_business_candidates,
    merge_in_memory_duplicates,
)
from candidates import candidates_from_search_results
from classification import ResultType
from content_extraction import empty_evidence, empty_signals, extract_business_signals
from enrichment import enrich_candidate
from enrichment_benchmark import (
    CountingFetcher,
    compare_snapshots,
    snapshot_business,
)
from local_llm import (
    LocalLLMClient,
    apply_unknown_fills,
    maybe_classify_with_local_llm,
)
from page_inspection import inspect_candidate
from searxng_provider import SearXNGSearchProvider

DEFAULT_QUERY = "women's fashion boutique Mumbai"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark rule-only vs rules + local Qwen for ambiguous cases."
    )
    parser.add_argument(
        "query",
        nargs="*",
        default=DEFAULT_QUERY.split(),
        help=f'Search query (default: "{DEFAULT_QUERY}")',
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Do not call Ollama (measures skip/disabled path only)",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Skip writing output/benchmark_llm_*.json",
    )
    return parser


def _is_invalid_source(row: BusinessCandidate) -> bool:
    signals = list((row.evidence or {}).get("signals") or [])
    if row.source_type in {
        ResultType.DIRECTORY.value,
        ResultType.ARTICLE.value,
        ResultType.SOCIAL.value,
        ResultType.VIDEO.value,
    }:
        return True
    return any(item in signals for item in ("roundup_page", "publisher_not_boutique"))


def _is_valid_business(row: BusinessCandidate) -> bool:
    if _is_invalid_source(row):
        return False
    return row.business_name != UNKNOWN and row.business_type is not BusinessType.UNKNOWN


def identification_block(rows: list[BusinessCandidate]) -> dict:
    return {
        "after_dedup": len(rows),
        "valid_business_entities": sum(1 for row in rows if _is_valid_business(row)),
        "invalid_article_or_directory": sum(1 for row in rows if _is_invalid_source(row)),
        "business_type": _enum_count(rows, "business_type", BusinessType),
        "women_fashion": _enum_count(rows, "women_fashion_relevance", Relevance),
        "physical_store": _enum_count(rows, "physical_store", PhysicalStore),
        "confidence": _enum_count(rows, "confidence", Confidence),
        "contact": {
            "Website": sum(1 for row in rows if _has_value(row.website)),
            "Instagram": sum(1 for row in rows if _has_value(row.instagram)),
            "Phone": sum(1 for row in rows if _has_value(row.phone)),
            "Email": sum(1 for row in rows if _has_value(row.email)),
            "Address": sum(1 for row in rows if _has_value(row.address)),
            "City": sum(1 for row in rows if _has_value(row.city)),
        },
    }


def run_llm_benchmark(query: str, limit: int, *, enable_llm: bool) -> dict:
    provider = SearXNGSearchProvider.from_env()
    if provider is None:
        raise RuntimeError("SEARXNG_URL is not set. Copy .env.example to .env.")
    started = time.monotonic()
    raw_results, search_error = collect_search_results(provider, query, limit)
    if search_error and not raw_results:
        raise RuntimeError(search_error)
    candidates = candidates_from_search_results(raw_results, query)[:limit]
    fetcher = CountingFetcher(retries=0, delay_seconds=1.0)
    client = LocalLLMClient(enabled=enable_llm)
    errors: list[dict] = []
    if search_error:
        errors.append({"stage": "search", "error": search_error})

    rules_raw: list[BusinessCandidate] = []
    llm_raw: list[BusinessCandidate] = []
    pair_records: list[dict] = []
    qwen_calls = 0
    qwen_seconds = 0.0
    ai_failures = 0
    skipped_confident = 0
    skipped_other = 0
    scraper_failures = 0

    total = len(candidates)
    for index, candidate in enumerate(candidates, start=1):
        print(
            f"[{index}/{total}] {candidate.result_type.value} {candidate.normalized_url}",
            flush=True,
        )
        enriched = None
        if candidate.result_type is ResultType.WEBSITE:
            enriched = enrich_candidate(fetcher, candidate)
            if enriched.pages:
                home = enriched.pages[0]
                signals = extract_business_signals(home)
                page = home
                rows = identify_business_candidates(
                    candidate, home, signals, enriched=enriched
                )
            else:
                scraper_failures += 1
                errors.append(
                    {
                        "stage": "homepage",
                        "url": candidate.normalized_url,
                        "error": "homepage fetch failed",
                    }
                )
                page = empty_evidence(
                    candidate.normalized_url or candidate.url, title=candidate.title
                )
                signals = empty_signals()
                rows = identify_business_candidates(candidate, page, signals)
        else:
            snapshot = inspect_candidate(fetcher, candidate)
            if not snapshot.fetch.fetched:
                scraper_failures += 1
                errors.append(
                    {
                        "stage": "fetch",
                        "url": snapshot.fetch.requested_url,
                        "error": snapshot.fetch.error,
                    }
                )
            page = snapshot.evidence
            signals = snapshot.signals
            rows = identify_business_candidates(
                snapshot.candidate, snapshot.evidence, snapshot.signals
            )

        attached_rows: list[BusinessCandidate] = []
        applied_rows: list[BusinessCandidate] = []
        page_for_llm = (
            enriched.as_page_evidence() if enriched is not None and enriched.pages else page
        )
        signals_for_llm = (
            enriched.combined_signals() if enriched is not None and enriched.pages else signals
        )
        for row in rows:
            attached, result = maybe_classify_with_local_llm(
                client,
                candidate=candidate,
                row=row,
                page_evidence=page_for_llm,
                signals=signals_for_llm,
                enriched=enriched,
            )
            if result.attempted:
                qwen_calls += 1
                qwen_seconds += result.elapsed_seconds
                if result.error:
                    ai_failures += 1
            elif result.skipped_reason == "rules_already_confident":
                skipped_confident += 1
            elif result.skipped_reason and result.skipped_reason != "disabled":
                skipped_other += 1
            attached_rows.append(attached)
            applied_rows.append(apply_unknown_fills(attached))
        rules_raw.extend(attached_rows)
        llm_raw.extend(applied_rows)

        rules_owner = attached_rows[0] if attached_rows else None
        llm_owner = applied_rows[0] if applied_rows else None
        pair_records.append(
            {
                "candidate": candidate_to_dict(candidate),
                "rules": snapshot_business(rules_owner),
                "llm_applied": snapshot_business(llm_owner),
                "comparison": compare_snapshots(
                    snapshot_business(rules_owner),
                    snapshot_business(llm_owner),
                ),
                "ai": (rules_owner.evidence or {}).get("ai") if rules_owner else None,
            }
        )

    rules_deduped = merge_in_memory_duplicates(rules_raw)
    llm_deduped = merge_in_memory_duplicates(llm_raw)
    elapsed = round(time.monotonic() - started, 1)
    changed = sum(1 for item in pair_records if item["comparison"]["changed"] or item["comparison"]["gained"] or item["comparison"]["lost"])
    improved = sum(1 for item in pair_records if item["comparison"]["gained"] and not item["comparison"]["worse"])
    weakened = sum(1 for item in pair_records if item["comparison"]["worse"])

    report = {
        "query": query,
        "limit": limit,
        "runtime_seconds": elapsed,
        "llm": {
            "enabled": enable_llm,
            "model": client.model,
            "ollama_url": client.base_url,
            "qwen_calls": qwen_calls,
            "qwen_processing_seconds": round(qwen_seconds, 2),
            "ai_failures": ai_failures,
            "skipped_rules_already_confident": skipped_confident,
            "skipped_other": skipped_other,
            "records_changed_by_ai": changed,
            "records_improved": improved,
            "records_weakened": weakened,
        },
        "scraper_failures": scraper_failures,
        "counts": {
            "search": {"raw_search_results": len(raw_results)},
            "discovery": {"candidates": len(candidates)},
            "A_rules_only": identification_block(rules_deduped),
            "B_rules_plus_qwen": identification_block(llm_deduped),
        },
        "pair_records": pair_records,
        "business_candidates_rules": [business_to_dict(row) for row in rules_deduped],
        "business_candidates_llm": [business_to_dict(row) for row in llm_deduped],
        "errors": errors,
    }
    return report


def write_json(report: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"benchmark_llm_{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def _print_ident(title: str, block: dict) -> None:
    print(title)
    print(f"Deduplicated: {block['after_dedup']}")
    print(f"Valid business entities: {block['valid_business_entities']}")
    print(f"Invalid article/directory entities: {block['invalid_article_or_directory']}")
    print(f"business_type: {block['business_type']}")
    print(f"women_fashion: {block['women_fashion']}")
    print(f"physical_store: {block['physical_store']}")
    print(f"confidence: {block['confidence']}")
    print(f"contact: {block['contact']}")
    print()


def print_summary(report: dict) -> None:
    llm = report["llm"]
    print("=== RULES vs RULES + LOCAL QWEN ===")
    print(f"Query: {report['query']}")
    print(f"Limit: {report['limit']}")
    print(f"Runtime: {report['runtime_seconds']}s")
    print(f"Qwen enabled: {llm['enabled']} model={llm['model']}")
    print()
    _print_ident("A. RULES ONLY", report["counts"]["A_rules_only"])
    _print_ident("B. RULES + QWEN (UNKNOWN fills only)", report["counts"]["B_rules_plus_qwen"])
    print("QWEN")
    print(f"Calls: {llm['qwen_calls']}")
    print(f"Processing time: {llm['qwen_processing_seconds']}s")
    print(f"AI failures: {llm['ai_failures']}")
    print(f"Skipped (rules already confident): {llm['skipped_rules_already_confident']}")
    print(f"Skipped (other): {llm['skipped_other']}")
    print(f"Records changed by AI: {llm['records_changed_by_ai']}")
    print(f"Records improved: {llm['records_improved']}")
    print(f"Records weakened: {llm['records_weakened']}")
    print(f"Scraper failures: {report['scraper_failures']}")
    print()
    print("Increases are not automatically better. Compare A vs B before trusting Qwen fills.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 1:
        print("--limit must be at least 1", file=sys.stderr)
        return 1
    query = " ".join(args.query).strip() or DEFAULT_QUERY
    try:
        report = run_llm_benchmark(query, args.limit, enable_llm=not args.no_llm)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print()
    print_summary(report)
    if not args.no_json:
        path = write_json(report)
        print()
        print(f"JSON report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
