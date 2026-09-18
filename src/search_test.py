"""CLI search test: query a configured SearXNG instance and print results.

Usage:
    python src/search_test.py "women's fashion boutique Mumbai"

Does not scrape result websites or extract contact fields.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from searxng_provider import SearXNGSearchProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send a query to SearXNG and print JSON search hits."
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
        print("Results: 0")
        return 1

    print(f"Results: {len(results)}")
    print()
    if not results:
        print("No search results returned.")
        return 0

    for index, result in enumerate(results, start=1):
        title = result.title or "(no title)"
        url = result.url or "(no url)"
        snippet = result.snippet or "(no snippet)"
        print(f"{index}. {title}")
        print(f"   {url}")
        print(f"   {snippet}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
