"""CLI: fetch one public page (or a small search sample) and print evidence.

Usage:
    python src/fetch_test.py https://example.com
    python src/fetch_test.py --from-search "women's fashion boutique Mumbai" --limit 5

Does not crawl, identify boutiques, or write to SQLite/Excel.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from candidates import Candidate, candidates_from_search_results
from classification import ResultType
from fetcher import PageFetcher
from page_inspection import candidate_from_url, inspect_candidate
from searxng_provider import SearXNGSearchProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a public page and print extracted business evidence."
    )
    parser.add_argument(
        "target",
        nargs="*",
        help="URL to fetch, or a search query when --from-search is set",
    )
    parser.add_argument(
        "--from-search",
        action="store_true",
        help="Treat the argument as a SearXNG query and fetch a small sample",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max candidates to fetch with --from-search (default: 5)",
    )
    parser.add_argument(
        "--no-robots",
        action="store_true",
        help="Skip robots.txt checks (tests only; default is to respect robots.txt)",
    )
    return parser


def pick_sample(candidates: list[Candidate], limit: int) -> list[Candidate]:
    """Prefer a mix of WEBSITE, DIRECTORY, SOCIAL, VIDEO, then another WEBSITE."""
    preferred = [
        ResultType.WEBSITE,
        ResultType.DIRECTORY,
        ResultType.SOCIAL,
        ResultType.VIDEO,
        ResultType.WEBSITE,
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
    for candidate in candidates:
        if len(picked) >= limit:
            break
        if id(candidate) in used:
            continue
        picked.append(candidate)
        used.add(id(candidate))
    return picked


def print_snapshot(snapshot) -> None:
    fetch = snapshot.fetch
    evidence = snapshot.evidence
    signals = snapshot.signals
    print(f"Type: {snapshot.candidate.result_type.value}")
    print(f"URL: {fetch.requested_url}")
    print(f"HTTP status: {fetch.status_code if fetch.status_code is not None else '(none)'}")
    print(f"Final URL: {fetch.final_url or '(none)'}")
    print(f"Content type: {fetch.content_type or '(none)'}")
    print(f"Fetched: {fetch.fetched}")
    if fetch.error:
        print(f"Error: {fetch.error}")
    print(f"Title: {evidence.title or '(none)'}")
    print(f"Meta description: {evidence.meta_description or '(none)'}")
    print(f"Number of links: {len(evidence.links)}")
    print(f"Emails found: {', '.join(signals.emails) if signals.emails else '(none)'}")
    print(f"Phones found: {', '.join(signals.phones) if signals.phones else '(none)'}")
    print(f"Social URLs found: {', '.join(signals.social_urls) if signals.social_urls else '(none)'}")
    print(f"WhatsApp URLs found: {', '.join(signals.whatsapp_urls) if signals.whatsapp_urls else '(none)'}")
    print(
        "Address candidates: "
        + ("; ".join(signals.address_candidates) if signals.address_candidates else "(none)")
    )
    print(
        "City mentions: "
        + (", ".join(signals.city_mentions) if signals.city_mentions else "(none)")
    )
    print(f"Extracted text length: {len(evidence.text)}")
    print(f"Elapsed seconds: {fetch.elapsed_seconds:.2f}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.target:
        print("Provide a URL, or --from-search plus a query.", file=sys.stderr)
        return 1

    fetcher = PageFetcher(
        respect_robots=not args.no_robots,
        delay_seconds=1.0,
        retries=0,
    )

    if args.from_search:
        query = " ".join(args.target)
        provider = SearXNGSearchProvider.from_env()
        if provider is None:
            print("SEARXNG_URL is not set. Copy .env.example to .env.", file=sys.stderr)
            return 1
        results = provider.search(query, page=1)
        if provider.last_error:
            print(f"Search failed: {provider.last_error}", file=sys.stderr)
            return 1
        candidates = candidates_from_search_results(results, query)
        sample = pick_sample(candidates, args.limit)
        print(f"Search candidates: {len(candidates)}")
        print(f"Fetching sample: {len(sample)}")
        print()
        succeeded = 0
        failed = 0
        for index, candidate in enumerate(sample, start=1):
            print(f"--- {index}/{len(sample)} ---")
            snapshot = inspect_candidate(fetcher, candidate)
            print_snapshot(snapshot)
            if snapshot.fetch.fetched:
                succeeded += 1
            else:
                failed += 1
            print()
        print(f"Succeeded: {succeeded}")
        print(f"Failed: {failed}")
        return 0 if succeeded else 1

    url = " ".join(args.target)
    snapshot = inspect_candidate(fetcher, candidate_from_url(url))
    print_snapshot(snapshot)
    return 0 if snapshot.fetch.fetched or snapshot.candidate.result_type in {
        ResultType.SOCIAL,
        ResultType.VIDEO,
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
