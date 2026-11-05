"""CLI: fetch a small candidate sample and identify BusinessCandidates.

Usage:
    python src/identify_test.py --from-search "women's fashion boutique Mumbai"

Does not crawl linked businesses or write to SQLite/Excel.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from business_candidates import identify_business_candidates
from candidates import Candidate, candidates_from_search_results
from classification import ResultType
from fetcher import PageFetcher
from page_inspection import inspect_candidate
from searxng_provider import SearXNGSearchProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Identify businesses from a small SearXNG candidate sample."
    )
    parser.add_argument(
        "query",
        nargs="+",
        help='Search query, for example: "women\'s fashion boutique Mumbai"',
    )
    parser.add_argument("--limit", type=int, default=6)
    return parser


def pick_sample(candidates: list[Candidate], limit: int) -> list[Candidate]:
    preferred = [
        ResultType.WEBSITE,
        ResultType.WEBSITE,
        ResultType.DIRECTORY,
        ResultType.DIRECTORY,
        ResultType.SOCIAL,
        ResultType.VIDEO,
    ]
    picked: list[Candidate] = []
    used: set[int] = set()
    for wanted in preferred:
        if len(picked) >= limit:
            break
        for candidate in candidates:
            if id(candidate) in used:
                continue
            if candidate.result_type is wanted:
                picked.append(candidate)
                used.add(id(candidate))
                break
    return picked


def print_business(index: int, row) -> None:
    print(f"  {index}. {row.business_name}")
    print(f"     type={row.business_type.value} fashion={row.fashion_relevance.value}")
    print(
        f"     women={row.women_fashion_relevance.value} "
        f"store={row.physical_store.value} city={row.city}"
    )
    print(f"     website={row.website or '(none)'}")
    print(f"     instagram={row.instagram or '(none)'}")
    print(f"     phone={row.phone or '(none)'} email={row.email or '(none)'}")
    print(f"     confidence={row.confidence.value}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    query = " ".join(args.query)
    provider = SearXNGSearchProvider.from_env()
    if provider is None:
        print("SEARXNG_URL is not set.", file=sys.stderr)
        return 1
    results = provider.search(query, page=1)
    if provider.last_error:
        print(f"Search failed: {provider.last_error}", file=sys.stderr)
        return 1
    candidates = candidates_from_search_results(results, query)
    sample = pick_sample(candidates, args.limit)
    fetcher = PageFetcher(retries=0, delay_seconds=1.0)
    print(f"Search candidates: {len(candidates)}")
    print(f"Identify sample: {len(sample)}")
    print()
    total = 0
    for candidate in sample:
        snapshot = inspect_candidate(fetcher, candidate)
        businesses = identify_business_candidates(
            snapshot.candidate, snapshot.evidence, snapshot.signals
        )
        total += len(businesses)
        print(f"Candidate type: {candidate.result_type.value}")
        print(f"Source URL: {candidate.normalized_url}")
        print(f"Fetched: {snapshot.fetch.fetched} {snapshot.fetch.error or ''}".strip())
        print(f"BusinessCandidates: {len(businesses)}")
        for index, row in enumerate(businesses[:12], start=1):
            print_business(index, row)
        if len(businesses) > 12:
            print(f"  ... {len(businesses) - 12} more")
        print()
    print(f"Total BusinessCandidates from sample: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
