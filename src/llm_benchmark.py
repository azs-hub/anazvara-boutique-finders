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
    is_rules_confident,
    maybe_classify_with_local_llm,
)
from page_inspection import inspect_candidate
from searxng_provider import SearXNGSearchProvider

DEFAULT_QUERY = "women's fashion boutique Mumbai"
STOCKIST_REFERENCE_EXAMPLES = (
    {"label": "Villa Mor", "expected": "YES", "needles": ("villa mor", "villamor")},
    {"label": "Rozina", "expected": "NO", "needles": ("rozina",)},
    {
        "label": "Yellow House Parra",
        "expected": "YES",
        "needles": ("yellow house parra", "yellowhouseparra"),
    },
    {"label": "Rangeela Goa", "expected": "YES", "needles": ("rangeela goa", "rangeelagoa")},
    {
        "label": "Paper Boat Collective",
        "expected": "YES",
        "needles": ("paper boat collective", "paperboatcollective"),
    },
)


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


def identification_block(rows: list[BusinessCandidate], *, after_ai: bool = False) -> dict:
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
        "potential_stockist": stockist_counts(rows, after_ai=after_ai),
    }


def stockist_of(row: BusinessCandidate | None, *, after_ai: bool) -> str:
    if row is None:
        return "UNKNOWN"
    evidence = row.evidence or {}
    key = "ai_potential_stockist" if after_ai else "rule_potential_stockist"
    value = str(evidence.get(key) or "UNKNOWN").upper()
    return value if value in {"YES", "NO", "UNKNOWN"} else "UNKNOWN"


def stockist_counts(rows: list[BusinessCandidate], *, after_ai: bool = True) -> dict[str, int]:
    counts = {"YES": 0, "NO": 0, "UNKNOWN": 0}
    for row in rows:
        counts[stockist_of(row, after_ai=after_ai)] += 1
    return counts


def compare_stockist(before: str, after: str, row: BusinessCandidate | None) -> dict:
    changed = before != after
    improved = False
    weakened = False
    if before == "UNKNOWN" and after == "YES":
        improved = True
    elif before == "UNKNOWN" and after == "NO":
        signals = list((row.evidence or {}).get("signals") or []) if row else []
        own_label = bool(
            row
            and (
                row.business_type.value in {"DESIGNER", "BRAND"}
                or (row.evidence or {}).get("ai_business_type")
                in {"DESIGNER", "OWN_BRAND", "WHOLESALE"}
            )
        )
        discovery = bool(
            row
            and (
                row.source_type in {"DIRECTORY", "ARTICLE", "SOCIAL", "VIDEO"}
                or any(item in signals for item in ("roundup_page", "publisher_not_boutique"))
            )
        )
        improved = own_label or discovery
    elif before == "YES" and after in {"NO", "UNKNOWN"}:
        weakened = True
    return {"changed": changed, "improved": improved, "weakened": weakened}


def _reference_haystack(row: BusinessCandidate) -> str:
    return " ".join(
        part
        for part in (
            row.business_name,
            row.website,
            row.source_url,
            str((row.evidence or {}).get("ai_business_name") or ""),
        )
        if part
    ).lower()


def match_stockist_references(rows: list[BusinessCandidate]) -> list[dict]:
    """Benchmark-only checks. Names are not used in production classification."""
    results = []
    for example in STOCKIST_REFERENCE_EXAMPLES:
        matches = [
            row
            for row in rows
            if any(needle in _reference_haystack(row) for needle in example["needles"])
        ]
        after_values = [stockist_of(row, after_ai=True) for row in matches]
        expected = example["expected"]
        results.append(
            {
                "label": example["label"],
                "expected_stockist": expected,
                "found": bool(matches),
                "matches": len(matches),
                "stockist_after_ai": after_values,
                "passed": bool(matches) and all(value == expected for value in after_values),
            }
        )
    return results


def evaluate_candidates(
    candidates: list,
    *,
    enable_llm: bool,
    query_label: str,
    extra_errors: list[dict] | None = None,
    raw_search_count: int = 0,
    limit: int | None = None,
    started: float | None = None,
) -> dict:
    """Fetch, identify, and optionally classify an already-built candidate list."""
    started = time.monotonic() if started is None else started
    fetcher = CountingFetcher(retries=0, delay_seconds=1.0)
    client = LocalLLMClient(enabled=enable_llm)
    errors: list[dict] = list(extra_errors or [])

    rules_raw: list[BusinessCandidate] = []
    llm_raw: list[BusinessCandidate] = []
    pair_records: list[dict] = []
    qwen_calls = 0
    qwen_seconds = 0.0
    http_failures = 0
    model_failures = 0
    schema_failures = 0
    validation_rejections = 0
    successful_qwen = 0
    skipped_other = 0
    identity_confident_sent = 0
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
                if result.failure_kind == "http":
                    http_failures += 1
                elif result.failure_kind == "model":
                    model_failures += 1
                elif result.failure_kind == "schema":
                    schema_failures += 1
                elif result.validated is not None:
                    successful_qwen += 1
                    if result.validated.get("rejected"):
                        validation_rejections += 1
            elif result.skipped_reason and result.skipped_reason != "disabled":
                skipped_other += 1
            if is_rules_confident(row) and result.attempted:
                identity_confident_sent += 1
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
                "stockist": compare_stockist(
                    stockist_of(rules_owner, after_ai=False),
                    stockist_of(llm_owner, after_ai=True),
                    llm_owner,
                ),
            }
        )

    rules_deduped = merge_in_memory_duplicates(rules_raw)
    llm_deduped = merge_in_memory_duplicates(llm_raw)
    elapsed = round(time.monotonic() - started, 1)
    field_changed = sum(1 for item in pair_records if item["comparison"]["changed"] or item["comparison"]["gained"] or item["comparison"]["lost"])
    field_improved = sum(1 for item in pair_records if item["comparison"]["gained"] and not item["comparison"]["worse"])
    field_weakened = sum(1 for item in pair_records if item["comparison"]["worse"])
    stockist_changed = 0
    stockist_improved = 0
    stockist_weakened = 0
    for attached, applied in zip(rules_raw, llm_raw, strict=True):
        cmp = compare_stockist(
            stockist_of(attached, after_ai=False),
            stockist_of(applied, after_ai=True),
            applied,
        )
        stockist_changed += int(cmp["changed"])
        stockist_improved += int(cmp["improved"])
        stockist_weakened += int(cmp["weakened"])
    ai_failures = http_failures + model_failures + schema_failures

    report = {
        "query": query_label,
        "limit": limit if limit is not None else len(candidates),
        "runtime_seconds": elapsed,
        "llm": {
            "enabled": enable_llm,
            "model": client.model,
            "ollama_url": client.base_url,
            "qwen_calls": qwen_calls,
            "successful_qwen_calls": successful_qwen,
            "qwen_processing_seconds": round(qwen_seconds, 2),
            "http_failures": http_failures,
            "model_failures": model_failures,
            "schema_failures": schema_failures,
            "validation_rejections": validation_rejections,
            "ai_failures": ai_failures,
            "identity_confident_sent_for_stockist": identity_confident_sent,
            "skipped_other": skipped_other,
            "records_changed_by_ai": stockist_changed,
            "records_improved": stockist_improved,
            "records_weakened": stockist_weakened,
            "field_fills_changed": field_changed,
            "field_fills_improved": field_improved,
            "field_fills_weakened": field_weakened,
        },
        "stockist": {
            "before_ai": stockist_counts(rules_deduped, after_ai=False),
            "after_ai": stockist_counts(llm_deduped, after_ai=True),
            "reference_examples": match_stockist_references(llm_deduped),
        },
        "scraper_failures": scraper_failures,
        "counts": {
            "search": {"raw_search_results": raw_search_count},
            "discovery": {"candidates": len(candidates)},
            "A_rules_only": identification_block(rules_deduped, after_ai=False),
            "B_rules_plus_qwen": identification_block(llm_deduped, after_ai=True),
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
    print(f"potential_stockist: {block['potential_stockist']}")
    print()


def print_summary(report: dict) -> None:
    llm = report["llm"]
    stockist = report["stockist"]
    print("=== RULES vs RULES + LOCAL QWEN ===")
    print(f"Query: {report['query']}")
    print(f"Limit: {report['limit']}")
    print(f"Runtime: {report['runtime_seconds']}s")
    print(f"Qwen enabled: {llm['enabled']} model={llm['model']}")
    print()
    _print_ident("A. RULES ONLY", report["counts"]["A_rules_only"])
    _print_ident("B. RULES + QWEN (UNKNOWN fills only)", report["counts"]["B_rules_plus_qwen"])
    print("POTENTIAL STOCKIST")
    print(f"Before AI: {stockist['before_ai']}")
    print(f"After AI:  {stockist['after_ai']}")
    print(f"YES before/after: {stockist['before_ai']['YES']} / {stockist['after_ai']['YES']}")
    print(f"NO before/after: {stockist['before_ai']['NO']} / {stockist['after_ai']['NO']}")
    print(f"UNKNOWN before/after: {stockist['before_ai']['UNKNOWN']} / {stockist['after_ai']['UNKNOWN']}")
    print(f"Records changed by AI: {llm['records_changed_by_ai']}")
    print(f"Records improved: {llm['records_improved']}")
    print(f"Records weakened: {llm['records_weakened']}")
    print(f"Records rejected by validation: {llm['validation_rejections']}")
    print()
    print("REFERENCE EXAMPLES (benchmark only, not ground truth)")
    for item in stockist["reference_examples"]:
        status = "PASS" if item["passed"] else ("MISSING" if not item["found"] else "FAIL")
        print(
            f"{item['label']}: expected {item['expected_stockist']}, "
            f"after={item['stockist_after_ai'] or '—'} [{status}]"
        )
    print()
    print("QWEN")
    print(f"Calls: {llm['qwen_calls']}")
    print(f"Successful calls: {llm['successful_qwen_calls']}")
    print(f"Processing time: {llm['qwen_processing_seconds']}s")
    print(f"HTTP/connection failures: {llm['http_failures']}")
    print(f"Model failures: {llm['model_failures']}")
    print(f"Schema/JSON failures: {llm['schema_failures']}")
    print(f"AI failures (http+model+schema): {llm['ai_failures']}")
    print(f"Identity-confident sent for stockist: {llm['identity_confident_sent_for_stockist']}")
    print(f"Skipped (other): {llm['skipped_other']}")
    print(f"Scraper failures: {report['scraper_failures']}")
    print()
    print("Stockist YES is the lead metric. More BOUTIQUE / HIGH women_fashion is not automatically better.")


def run_llm_benchmark(query: str, limit: int, *, enable_llm: bool) -> dict:
    provider = SearXNGSearchProvider.from_env()
    if provider is None:
        raise RuntimeError("SEARXNG_URL is not set. Copy .env.example to .env.")
    started = time.monotonic()
    raw_results, search_error = collect_search_results(provider, query, limit)
    if search_error and not raw_results:
        raise RuntimeError(search_error)
    candidates = candidates_from_search_results(raw_results, query)[:limit]
    errors: list[dict] = []
    if search_error:
        errors.append({"stage": "search", "error": search_error})
    return evaluate_candidates(
        candidates,
        enable_llm=enable_llm,
        query_label=query,
        extra_errors=errors,
        raw_search_count=len(raw_results),
        limit=limit,
        started=started,
    )


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
