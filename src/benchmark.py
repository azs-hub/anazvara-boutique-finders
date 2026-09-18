"""Benchmark the deterministic discovery + fetch + identification pipeline.

Usage:
    python src/benchmark.py "women's fashion boutique Mumbai" --limit 50

Reuses existing SearXNG, candidate, fetcher, and BusinessCandidate modules.
Does not crawl extracted links, write SQLite, or use AI.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

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
from candidates import Candidate, candidates_from_search_results, count_by_type
from classification import ResultType
from fetcher import PageFetcher
from page_inspection import inspect_candidate
from search_provider import SearchResult
from searxng_provider import SearXNGSearchProvider

DEFAULT_LIMIT = 50
MAX_SEARCH_PAGES = 15
OUTPUT_DIR = PROJECT_ROOT / "output"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark SearXNG → candidates → fetch → BusinessCandidates."
    )
    parser.add_argument(
        "query",
        nargs="+",
        help='Search query, for example: "women\'s fashion boutique Mumbai"',
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Max search-result candidates to fetch (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Skip writing output/benchmark_*.json",
    )
    return parser


def collect_search_results(
    provider: SearXNGSearchProvider,
    query: str,
    limit: int,
) -> tuple[list[SearchResult], str | None]:
    """Page through SearXNG until we can build ``limit`` candidates, or pages end."""
    collected: list[SearchResult] = []
    seen_urls: set[str] = set()
    for page in range(1, MAX_SEARCH_PAGES + 1):
        batch = provider.search(query, page=page)
        if provider.last_error:
            return collected, provider.last_error
        if not batch:
            break
        new_rows = 0
        for row in batch:
            key = row.url or f"{row.title}:{len(collected)}"
            if key in seen_urls:
                continue
            seen_urls.add(key)
            collected.append(row)
            new_rows += 1
        if new_rows == 0:
            break
        built = candidates_from_search_results(collected, query)
        if len(built) >= limit:
            break
    return collected, None


def _enum_count(rows: list[BusinessCandidate], attr: str, members) -> dict[str, int]:
    counts = Counter(getattr(row, attr).value for row in rows)
    return {member.value: counts.get(member.value, 0) for member in members}


def _result_type_counts(candidates: list[Candidate]) -> dict[str, int]:
    counts = count_by_type(candidates)
    return {item.value: counts.get(item, 0) for item in ResultType}


def _source_type_counts(rows: list[BusinessCandidate]) -> dict[str, int]:
    counts = Counter(row.source_type for row in rows)
    return {item.value: counts.get(item.value, 0) for item in ResultType}


def _has_value(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().upper() != UNKNOWN


def _unknown_reason(row: BusinessCandidate) -> str:
    signals = row.evidence.get("signals") if isinstance(row.evidence, dict) else None
    name_source = None
    if isinstance(row.evidence, dict):
        name_source = row.evidence.get("name_source")
    parts = []
    if row.business_name == UNKNOWN:
        parts.append(f"name={name_source or 'none'}")
    if row.business_type is BusinessType.UNKNOWN:
        parts.append("type=UNKNOWN")
    if row.women_fashion_relevance is Relevance.UNKNOWN:
        parts.append("women=UNKNOWN")
    if row.city == UNKNOWN:
        parts.append("city=UNKNOWN")
    if signals:
        parts.append("signals=" + ",".join(str(item) for item in signals[:6]))
    return "; ".join(parts) if parts else "insufficient evidence"


def _json_ready(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def business_to_dict(row: BusinessCandidate) -> dict:
    payload = asdict(row)
    return _json_ready(payload)


def candidate_to_dict(row: Candidate) -> dict:
    return {
        "title": row.title,
        "url": row.url,
        "normalized_url": row.normalized_url,
        "domain": row.domain,
        "result_type": row.result_type.value,
        "snippet": row.snippet,
        "search_source": row.search_source,
    }


def print_summary(report: dict) -> None:
    search = report["counts"]["search"]
    discovery = report["counts"]["discovery"]
    fetching = report["counts"]["fetching"]
    ident = report["counts"]["identification"]
    print("SEARCH")
    print(f"Query: {report['query']}")
    print()
    print("DISCOVERY")
    print(f"Raw search results: {search['raw_search_results']}")
    print(f"Candidates: {discovery['candidates']}")
    print()
    print("FETCHING")
    print(f"Fetched successfully: {fetching['fetched_successfully']}")
    print(f"Blocked/failed: {fetching['blocked_failed']}")
    print()
    print("BUSINESS IDENTIFICATION")
    print(f"BusinessCandidates: {ident['after_dedup']}")
    print()
    print("BUSINESS TYPE")
    for key, value in ident["business_type"].items():
        print(f"{key}: {value}")
    print()
    print("WOMEN'S FASHION")
    for key, value in ident["women_fashion"].items():
        print(f"{key}: {value}")
    print()
    print("PHYSICAL STORE")
    for key, value in ident["physical_store"].items():
        print(f"{key}: {value}")
    print()
    print("CONFIDENCE")
    for key, value in ident["confidence"].items():
        print(f"{key}: {value}")
    print()
    print("SOURCE BREAKDOWN (candidates)")
    for key, value in discovery["by_type"].items():
        print(f"{key}: {value}")
    print()
    print("SOURCE BREAKDOWN (BusinessCandidates)")
    for key, value in ident["by_source_type"].items():
        print(f"{key}: {value}")
    print()
    print("CONTACT DATA COVERAGE")
    for key, value in ident["contact"].items():
        print(f"{key}: {value}")
    print()
    print("DUPLICATION")
    print(f"Raw BusinessCandidates: {ident['raw']}")
    print(f"After in-memory deduplication: {ident['after_dedup']}")
    print()
    print("HIGH-CONFIDENCE (up to 20)")
    high = report["high_confidence"]
    if not high:
        print("(none)")
    for index, row in enumerate(high, start=1):
        print(f"{index}. {row['business_name']}")
        print(f"   type={row['business_type']} women={row['women_fashion_relevance']}")
        print(f"   store={row['physical_store']} city={row['city']}")
        print(f"   website={row['website'] or '(none)'}")
        print(f"   instagram={row['instagram'] or '(none)'}")
        print(f"   phone={row['phone'] or '(none)'} email={row['email'] or '(none)'}")
        print(f"   source={row['source_url']}")
    print()
    print("UNKNOWN / LOW-CONFIDENCE (up to 20)")
    unknown = report["unknown_or_low"]
    if not unknown:
        print("(none)")
    for index, row in enumerate(unknown, start=1):
        print(f"{index}. {row['business_name']}")
        print(f"   source_type={row['source_type']}")
        print(f"   source={row['source_url']}")
        print(f"   website={row['website'] or '(none)'}")
        print(f"   instagram={row['instagram'] or '(none)'} city={row['city']}")
        print(f"   why={row['why']}")


def run_benchmark(query: str, limit: int) -> dict:
    provider = SearXNGSearchProvider.from_env()
    if provider is None:
        raise RuntimeError(
            "SEARXNG_URL is not set. Copy .env.example to .env."
        )
    raw_results, search_error = collect_search_results(provider, query, limit)
    if search_error and not raw_results:
        raise RuntimeError(search_error)
    candidates = candidates_from_search_results(raw_results, query)[:limit]
    fetcher = PageFetcher(retries=0, delay_seconds=1.0)
    errors: list[dict] = []
    if search_error:
        errors.append({"stage": "search", "error": search_error})
    raw_businesses: list[BusinessCandidate] = []
    fetched_ok = 0
    fetched_fail = 0
    total = len(candidates)
    for index, candidate in enumerate(candidates, start=1):
        print(f"[{index}/{total}] {candidate.result_type.value} {candidate.normalized_url}", flush=True)
        snapshot = inspect_candidate(fetcher, candidate)
        if snapshot.fetch.fetched:
            fetched_ok += 1
        else:
            fetched_fail += 1
            errors.append(
                {
                    "stage": "fetch",
                    "url": snapshot.fetch.requested_url,
                    "result_type": candidate.result_type.value,
                    "status_code": snapshot.fetch.status_code,
                    "error": snapshot.fetch.error,
                }
            )
        rows = identify_business_candidates(
            snapshot.candidate, snapshot.evidence, snapshot.signals
        )
        raw_businesses.extend(rows)
    deduped = merge_in_memory_duplicates(raw_businesses)
    high = [row for row in deduped if row.confidence is Confidence.HIGH][:20]
    unknown = [
        row
        for row in deduped
        if row.confidence is Confidence.LOW
        or row.business_name == UNKNOWN
        or row.business_type is BusinessType.UNKNOWN
    ][:20]
    report = {
        "query": query,
        "limit": limit,
        "counts": {
            "search": {"raw_search_results": len(raw_results)},
            "discovery": {
                "candidates": len(candidates),
                "by_type": _result_type_counts(candidates),
            },
            "fetching": {
                "fetched_successfully": fetched_ok,
                "blocked_failed": fetched_fail,
            },
            "identification": {
                "raw": len(raw_businesses),
                "after_dedup": len(deduped),
                "business_type": _enum_count(deduped, "business_type", BusinessType),
                "women_fashion": _enum_count(
                    deduped, "women_fashion_relevance", Relevance
                ),
                "physical_store": _enum_count(deduped, "physical_store", PhysicalStore),
                "confidence": _enum_count(deduped, "confidence", Confidence),
                "by_source_type": _source_type_counts(deduped),
                "contact": {
                    "Website": sum(1 for row in deduped if _has_value(row.website)),
                    "Instagram": sum(1 for row in deduped if _has_value(row.instagram)),
                    "Phone": sum(1 for row in deduped if _has_value(row.phone)),
                    "Email": sum(1 for row in deduped if _has_value(row.email)),
                    "Address": sum(1 for row in deduped if _has_value(row.address)),
                    "City": sum(1 for row in deduped if _has_value(row.city)),
                },
            },
        },
        "candidates": [candidate_to_dict(row) for row in candidates],
        "business_candidates": [business_to_dict(row) for row in deduped],
        "errors": errors,
        "high_confidence": [business_to_dict(row) for row in high],
        "unknown_or_low": [
            {**business_to_dict(row), "why": _unknown_reason(row)} for row in unknown
        ],
    }
    return report


def write_json(report: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"benchmark_{stamp}.json"
    serializable = {
        "query": report["query"],
        "limit": report["limit"],
        "counts": report["counts"],
        "candidates": report["candidates"],
        "business_candidates": report["business_candidates"],
        "errors": report["errors"],
    }
    path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 1:
        print("--limit must be at least 1", file=sys.stderr)
        return 1
    query = " ".join(args.query)
    try:
        report = run_benchmark(query, args.limit)
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
