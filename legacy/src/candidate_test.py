"""CLI: SearXNG search → classify/normalize/dedupe candidates.

Usage:
    python src/candidate_test.py "women's fashion boutique Mumbai"

Does not scrape candidate websites or extract contact fields.
Does not expand directory/article pages.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from candidates import candidates_from_search_results, count_by_type
from classification import ResultType
from searxng_provider import SearXNGSearchProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify and deduplicate SearXNG hits into discovery candidates."
    )
    parser.add_argument(
        "query",
        nargs="+",
        help='Search query, for example: "women\'s fashion boutique Mumbai"',
    )
    parser.add_argument(
        "--page",
        type=int,
        default=1,
        help="SearXNG result page number (default: 1)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    query = " ".join(args.query)
    provider = SearXNGSearchProvider.from_env()
    if provider is None:
        print(
            "SEARXNG_URL is not set.\n"
            "Copy .env.example to .env and set SEARXNG_URL, for example:\n"
            "  SEARXNG_URL=http://localhost:8888",
            file=sys.stderr,
        )
        return 1

    print(f"SearXNG: {provider.base_url}")
    print(f"Query: {query}")
    print(f"Page: {args.page}")
    print()

    results = provider.search(query, page=args.page)
    if provider.last_error:
        print(f"Search failed: {provider.last_error}", file=sys.stderr)
        return 1

    candidates = candidates_from_search_results(results, query)
    counts = count_by_type(candidates)

    if not candidates:
        print("No candidates produced.")
    else:
        for index, candidate in enumerate(candidates, start=1):
            title = candidate.title or "(no title)"
            print(f"{index}. {candidate.result_type.value}")
            print(f"   {title}")
            print(f"   {candidate.normalized_url}")
            print(f"   domain: {candidate.domain}")
            print()

    print(f"Raw search results: {len(results)}")
    print(f"Unique candidate URLs: {len(candidates)}")
    print(f"Website candidates: {counts[ResultType.WEBSITE]}")
    print(f"Directory candidates: {counts[ResultType.DIRECTORY]}")
    print(f"Social candidates: {counts[ResultType.SOCIAL]}")
    print(f"Article candidates: {counts[ResultType.ARTICLE]}")
    print(f"Video candidates: {counts[ResultType.VIDEO]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
