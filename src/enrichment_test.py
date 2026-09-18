"""CLI: enrich a few WEBSITE candidates (depth 1, max 3 internal pages).

Usage:
    python -m src.enrichment_test
    python src/enrichment_test.py "women's fashion boutique Mumbai"

Does not crawl recursively, write SQLite/Excel, or change the benchmark.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from business_candidates import identify_business_candidates
from candidates import candidates_from_search_results
from classification import ResultType
from content_extraction import extract_business_signals
from enrichment import enrich_candidate
from fetcher import PageFetcher
from searxng_provider import SearXNGSearchProvider
from url_normalization import normalize_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Controlled enrichment test for a few WEBSITE candidates."
    )
    parser.add_argument(
        "query",
        nargs="*",
        default=["women's", "fashion", "boutique", "Mumbai"],
        help='Search query (default: "women\'s fashion boutique Mumbai")',
    )
    parser.add_argument("--limit", type=int, default=5)
    return parser


def print_row(index: int, candidate, enriched, row) -> None:
    sources = enriched.selected_url_sources or [
        {"url": url, "source": "unknown"} for url in enriched.selected_urls
    ]
    print(f"{index}. {candidate.normalized_url}")
    print(f"   sitemap discovered: {'yes' if enriched.sitemap_discovered else 'no'}")
    print(f"   sitemap source: {enriched.sitemap_source or '(none)'}")
    print(f"   sitemap URLs discovered: {enriched.sitemap_url_count}")
    print(f"   relevant sitemap URLs: {len(enriched.relevant_sitemap_urls)}")
    print(f"   selected: {enriched.selected_urls or '(none)'}")
    print(f"   selected sources: {sources or '(none)'}")
    print(f"   successful: {enriched.successful_urls or '(none)'}")
    print(f"   failed: {enriched.failed_urls or '(none)'}")
    print(f"   name={row.business_name}")
    print(f"   type={row.business_type.value} women={row.women_fashion_relevance.value}")
    print(f"   store={row.physical_store.value} city={row.city}")
    print(f"   address={row.address or '(none)'}")
    print(f"   phone={row.phone or '(none)'} email={row.email or '(none)'}")
    print(f"   instagram={row.instagram or '(none)'} confidence={row.confidence.value}")
    print()


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
    websites = [
        candidate
        for candidate in candidates
        if candidate.result_type is ResultType.WEBSITE
    ][: args.limit]
    fetcher = PageFetcher(retries=0, delay_seconds=1.0)

    selected_count = 0
    successful_extra = 0
    failed_extra = 0
    homepage_ok = 0
    homepage_fail = 0
    sitemap_yes = 0
    sitemap_urls_total = 0
    relevant_total = 0

    print(f"Query: {query}")
    print(f"WEBSITE candidates tested: {len(websites)}")
    print()

    for index, candidate in enumerate(websites, start=1):
        enriched = enrich_candidate(fetcher, candidate)
        selected_count += len(enriched.selected_urls)
        if enriched.sitemap_discovered:
            sitemap_yes += 1
        sitemap_urls_total += enriched.sitemap_url_count
        relevant_total += len(enriched.relevant_sitemap_urls)
        home = normalize_url(candidate.normalized_url or candidate.url)
        extras_ok = [
            url
            for url in enriched.successful_urls
            if normalize_url(url) != home
        ]
        successful_extra += len(extras_ok)
        failed_extra += len(
            [url for url in enriched.failed_urls if normalize_url(url) != home]
        )
        if any(normalize_url(url) == home for url in enriched.successful_urls):
            homepage_ok += 1
        elif any(normalize_url(url) == home for url in enriched.failed_urls):
            homepage_fail += 1
        if not enriched.pages:
            print(f"{index}. {candidate.normalized_url}")
            print(f"   sitemap discovered: {'yes' if enriched.sitemap_discovered else 'no'}")
            print(f"   sitemap source: {enriched.sitemap_source or '(none)'}")
            print(f"   sitemap URLs discovered: {enriched.sitemap_url_count}")
            print(f"   relevant sitemap URLs: {len(enriched.relevant_sitemap_urls)}")
            print(f"   selected: {enriched.selected_urls or '(none)'}")
            print(f"   successful: {enriched.successful_urls or '(none)'}")
            print(f"   failed: {enriched.failed_urls or '(none)'}")
            print("   (no page evidence)")
            print()
            continue
        home_page = enriched.pages[0]
        businesses = identify_business_candidates(
            candidate,
            home_page,
            extract_business_signals(home_page),
            enriched=enriched,
        )
        if not businesses:
            print(f"{index}. {candidate.normalized_url}")
            print("   (no BusinessCandidate)")
            print()
            continue
        print_row(index, candidate, enriched, businesses[0])

    print("Summary")
    print(f"  candidates: {len(websites)}")
    print(f"  homepages fetched: {homepage_ok}")
    print(f"  homepages failed: {homepage_fail}")
    print(f"  enrichment pages selected: {selected_count}")
    print(f"  enrichment fetches succeeded: {successful_extra}")
    print(f"  enrichment fetches failed: {failed_extra}")
    print(f"  sitemaps discovered: {sitemap_yes}/{len(websites)}")
    print(f"  sitemap URLs found: {sitemap_urls_total}")
    print(f"  relevant sitemap URLs: {relevant_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
