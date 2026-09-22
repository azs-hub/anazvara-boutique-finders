"""Measure controlled enrichment against the Step 6.5 homepage-only baseline.

Usage:
    python src/enrichment_benchmark.py "women's fashion boutique Mumbai" --limit 50
    python src/enrichment_benchmark.py "women's fashion boutique Mumbai" --limit 100
    python src/enrichment_benchmark.py "women's fashion boutique Mumbai" --limits 50,100

Does not use AI, SQLite, Playwright, or paid APIs.
Does not rewrite the homepage-only ``benchmark.py`` pipeline.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

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
    _result_type_counts,
    _source_type_counts,
)
from business_candidates import (
    UNKNOWN,
    CHROME_NAME_RE,
    GENERIC_NAMES,
    ROUNDUP_TITLE_RE,
    BusinessCandidate,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
    identify_business_candidates,
    merge_in_memory_duplicates,
)
from candidates import Candidate, candidates_from_search_results
from classification import DIRECTORY_DOMAINS, ResultType, _host_matches
from content_extraction import (
    CITY_NAMES,
    empty_evidence,
    empty_signals,
    extract_business_signals,
)
from enrichment import enrich_candidate
from fetcher import PageFetcher
from page_inspection import inspect_candidate
from searxng_provider import SearXNGSearchProvider
from url_normalization import extract_domain

DEFAULT_QUERY = "women's fashion boutique Mumbai"
PREFERRED_BASELINES = {
    50: "benchmark_20260918_124008.json",
    100: "benchmark_20260918_124334.json",
}
MUMBAI_FAMILY = {
    "mumbai",
    "navi mumbai",
    "thane",
    "bandra",
    "khar",
    "colaba",
}
PRODUCT_PATH_RE = re.compile(
    r"/(products?|collections?|item|sku)(/|$)",
    re.IGNORECASE,
)
PUBLISHER_RE = re.compile(
    r"\b(times of india|hindustan times|indian express|vogue|elle|"
    r"lifestyleasia|time\s*out|tripadvisor|yelp|justdial|sulekha|"
    r"wikipedia|medium\.com)\b",
    re.IGNORECASE,
)
CONFIDENCE_RANK = {
    Confidence.LOW.value: 0,
    Confidence.MEDIUM.value: 1,
    Confidence.HIGH.value: 2,
}
SNAPSHOT_FIELDS = (
    "business_name",
    "business_type",
    "women_fashion_relevance",
    "physical_store",
    "city",
    "address",
    "website",
    "instagram",
    "phone",
    "email",
    "confidence",
    "source_type",
    "source_url",
)
GAIN_FIELDS = (
    "business_name",
    "address",
    "city",
    "phone",
    "email",
    "instagram",
    "physical_store",
    "business_type",
    "women_fashion_relevance",
)
COMPARISON_METRICS = (
    ("candidates", "Candidates"),
    ("raw_business_candidates", "Raw business candidates"),
    ("deduplicated_candidates", "Deduplicated candidates"),
    ("high_confidence", "HIGH confidence"),
    ("physical_store_yes", "Physical stores YES"),
    ("city_populated", "City populated"),
    ("address_populated", "Address populated"),
    ("phone_populated", "Phone populated"),
    ("email_populated", "Email populated"),
    ("instagram_populated", "Instagram populated"),
    ("high_women_fashion", "HIGH women-fashion relevance"),
    ("total_fetches", "Total fetches"),
    ("failed_fetches", "Failed fetches"),
    ("runtime_seconds", "Runtime (seconds)"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark homepage-only vs controlled enrichment identification."
    )
    parser.add_argument(
        "query",
        nargs="*",
        default=DEFAULT_QUERY.split(),
        help=f'Search query (default: "{DEFAULT_QUERY}")',
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max search-result candidates for a single run",
    )
    parser.add_argument(
        "--limits",
        default=None,
        help="Comma-separated candidate limits, for example: 50,100",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="Path to a Step 6.5 benchmark JSON file (optional)",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Skip writing output/benchmark_enriched_*.json",
    )
    return parser


def parse_limits(limit: int | None, limits: str | None) -> list[int]:
    if limits:
        values = [int(part.strip()) for part in limits.split(",") if part.strip()]
        if not values or any(value < 1 for value in values):
            raise ValueError("--limits must be a comma-separated list of positive integers")
        return values
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be at least 1")
        return [limit]
    return [50, 100]


def field_populated(value) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.upper() != UNKNOWN


def snapshot_business(row: BusinessCandidate | None) -> dict:
    if row is None:
        return {field: None for field in SNAPSHOT_FIELDS}
    return {
        "business_name": row.business_name,
        "business_type": row.business_type.value,
        "women_fashion_relevance": row.women_fashion_relevance.value,
        "physical_store": row.physical_store.value,
        "city": row.city,
        "address": row.address,
        "website": row.website,
        "instagram": row.instagram,
        "phone": row.phone,
        "email": row.email,
        "confidence": row.confidence.value,
        "source_type": row.source_type,
        "source_url": row.source_url,
    }


def _values_differ(before, after) -> bool:
    left = "" if before is None else str(before).strip().lower()
    right = "" if after is None else str(after).strip().lower()
    return left != right


def compare_snapshots(before: dict, after: dict) -> dict:
    """Compare homepage-only vs enriched field snapshots for one owner row."""
    gained: list[str] = []
    lost: list[str] = []
    changed: list[str] = []
    worse: list[str] = []
    details: dict[str, dict] = {}
    for field in GAIN_FIELDS:
        prev = before.get(field)
        nxt = after.get(field)
        prev_ok = field_populated(prev)
        next_ok = field_populated(nxt)
        if not prev_ok and next_ok:
            gained.append(field)
        elif prev_ok and not next_ok:
            lost.append(field)
            worse.append(field)
        elif prev_ok and next_ok and _values_differ(prev, nxt):
            changed.append(field)
            if field == "physical_store" and prev == PhysicalStore.YES.value:
                worse.append(field)
            if field == "business_name" and _name_looks_worse(str(nxt)):
                worse.append(field)
        details[field] = {"before": prev, "after": nxt}
    before_high = before.get("confidence") == Confidence.HIGH.value
    after_high = after.get("confidence") == Confidence.HIGH.value
    if after_high and not before_high:
        gained.append("high_confidence")
    elif before_high and not after_high:
        lost.append("high_confidence")
        worse.append("confidence")
    elif _values_differ(before.get("confidence"), after.get("confidence")):
        changed.append("confidence")
        before_rank = CONFIDENCE_RANK.get(str(before.get("confidence") or ""), 0)
        after_rank = CONFIDENCE_RANK.get(str(after.get("confidence") or ""), 0)
        if after_rank < before_rank:
            worse.append("confidence")
    details["confidence"] = {
        "before": before.get("confidence"),
        "after": after.get("confidence"),
    }
    return {
        "gained": gained,
        "lost": lost,
        "changed": changed,
        "worse": worse,
        "details": details,
    }


def _name_looks_worse(name: str) -> bool:
    lowered = name.strip().lower()
    return bool(CHROME_NAME_RE.search(name)) or lowered in GENERIC_NAMES


def query_city_family(query: str) -> set[str]:
    lowered = query.lower()
    if "mumbai" in lowered:
        return set(MUMBAI_FAMILY)
    family = set()
    for city in CITY_NAMES:
        if city.lower() in lowered:
            family.add(city.lower())
    return family


def suspicious_reasons(
    row: dict,
    *,
    query: str,
    candidate_title: str = "",
) -> list[str]:
    """Heuristic flags only. Does not change classification."""
    reasons: list[str] = []
    name = str(row.get("business_name") or "")
    website = str(row.get("website") or "")
    city = str(row.get("city") or "")
    source_type = str(row.get("source_type") or "")
    business_type = str(row.get("business_type") or "")
    physical = str(row.get("physical_store") or "")
    domain = extract_domain(website) or extract_domain(str(row.get("source_url") or "")) or ""
    title = candidate_title or name

    if CHROME_NAME_RE.search(name):
        reasons.append("ecommerce_ui_name")
    if name.strip().lower() in GENERIC_NAMES:
        reasons.append("generic_page_title")
    if ROUNDUP_TITLE_RE.search(title) or ROUNDUP_TITLE_RE.search(name):
        reasons.append("roundup_article")
    if PUBLISHER_RE.search(title) or PUBLISHER_RE.search(name) or PUBLISHER_RE.search(domain):
        reasons.append("roundup_or_publisher")
    if source_type == ResultType.DIRECTORY.value or _host_matches(domain, DIRECTORY_DOMAINS):
        reasons.append("directory")
    if business_type == BusinessType.MARKETPLACE.value:
        reasons.append("marketplace")
    path = urlparse(website).path if website else ""
    if PRODUCT_PATH_RE.search(path):
        reasons.append("product_page")
    family = query_city_family(query)
    if field_populated(city) and family and city.strip().lower() not in family:
        reasons.append("unrelated_city")
    if business_type == BusinessType.BRAND.value and physical != PhysicalStore.YES.value:
        if not field_populated(row.get("address")):
            reasons.append("brand_without_store_evidence")
    if business_type == BusinessType.UNKNOWN.value and row.get("confidence") == Confidence.HIGH.value:
        reasons.append("high_confidence_unknown_type")
    if source_type in {ResultType.ARTICLE.value, ResultType.VIDEO.value}:
        reasons.append("article_or_video_source")
    return reasons


def primary_row(
    rows: list[BusinessCandidate],
    candidate: Candidate,
) -> BusinessCandidate | None:
    if not rows:
        return None
    domain = candidate.domain
    for row in rows:
        website_domain = extract_domain(row.website or "")
        if website_domain and website_domain == domain:
            return row
    return rows[0]


class CountingFetcher(PageFetcher):
    """PageFetcher that records HTML, sitemap, robots, and raw HTTP traffic."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.http_requests: list[dict] = []
        self.fetch_calls: list[dict] = []
        original_get = self.session.get

        def counted_get(*args, **kwargs):
            url = args[0] if args else kwargs.get("url", "")
            started = time.monotonic()
            try:
                response = original_get(*args, **kwargs)
            except Exception as exc:
                self.http_requests.append(
                    {
                        "url": str(url),
                        "ok": False,
                        "status_code": None,
                        "error": str(exc),
                        "kind": http_kind(str(url)),
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    }
                )
                raise
            status = getattr(response, "status_code", None)
            ok = bool(status is not None and status < 400)
            self.http_requests.append(
                {
                    "url": str(url),
                    "ok": ok,
                    "status_code": status,
                    "error": None if ok else f"HTTP {status}",
                    "kind": http_kind(str(url)),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
            return response

        self.session.get = counted_get  # type: ignore[method-assign]

    def fetch(self, target, *, allow_xml: bool = False):
        result = super().fetch(target, allow_xml=allow_xml)
        self.fetch_calls.append(
            {
                "url": result.requested_url,
                "fetched": result.fetched,
                "error": result.error,
                "allow_xml": allow_xml,
                "kind": "sitemap" if allow_xml else "html",
            }
        )
        return result


def http_kind(url: str) -> str:
    lowered = url.lower()
    path = urlparse(url).path.lower()
    if path.endswith("/robots.txt") or path.endswith("robots.txt"):
        return "robots"
    if "sitemap" in lowered:
        return "sitemap"
    return "html"


def empty_gain_totals() -> dict[str, dict[str, int]]:
    keys = list(GAIN_FIELDS) + ["high_confidence"]
    return {
        "gained": {key: 0 for key in keys},
        "lost": {key: 0 for key in keys},
        "changed": {key: 0 for key in keys},
        "worse": {key: 0 for key in keys},
    }


def add_gain(totals: dict[str, dict[str, int]], comparison: dict) -> None:
    for bucket in ("gained", "lost", "changed", "worse"):
        for field in comparison.get(bucket, []):
            if field in totals[bucket]:
                totals[bucket][field] += 1


def identification_counts(rows: list[BusinessCandidate]) -> dict:
    return {
        "raw": None,
        "after_dedup": len(rows),
        "business_type": _enum_count(rows, "business_type", BusinessType),
        "women_fashion": _enum_count(rows, "women_fashion_relevance", Relevance),
        "physical_store": _enum_count(rows, "physical_store", PhysicalStore),
        "confidence": _enum_count(rows, "confidence", Confidence),
        "by_source_type": _source_type_counts(rows),
        "contact": {
            "Website": sum(1 for row in rows if _has_value(row.website)),
            "Instagram": sum(1 for row in rows if _has_value(row.instagram)),
            "Phone": sum(1 for row in rows if _has_value(row.phone)),
            "Email": sum(1 for row in rows if _has_value(row.email)),
            "Address": sum(1 for row in rows if _has_value(row.address)),
            "City": sum(1 for row in rows if _has_value(row.city)),
        },
    }


def comparison_metrics_from_counts(
    *,
    candidates: int,
    raw: int,
    deduped: int,
    ident: dict,
    total_fetches,
    failed_fetches,
    runtime_seconds,
) -> dict:
    return {
        "candidates": candidates,
        "raw_business_candidates": raw,
        "deduplicated_candidates": deduped,
        "high_confidence": ident.get("confidence", {}).get("HIGH", 0),
        "physical_store_yes": ident.get("physical_store", {}).get("YES", 0),
        "city_populated": ident.get("contact", {}).get("City", 0),
        "address_populated": ident.get("contact", {}).get("Address", 0),
        "phone_populated": ident.get("contact", {}).get("Phone", 0),
        "email_populated": ident.get("contact", {}).get("Email", 0),
        "instagram_populated": ident.get("contact", {}).get("Instagram", 0),
        "high_women_fashion": ident.get("women_fashion", {}).get("HIGH", 0),
        "total_fetches": total_fetches,
        "failed_fetches": failed_fetches,
        "runtime_seconds": runtime_seconds,
    }


def find_baseline(limit: int, query: str, explicit: str | None = None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    preferred = OUTPUT_DIR / PREFERRED_BASELINES.get(limit, "")
    if preferred.is_file():
        return preferred
    matches: list[Path] = []
    if not OUTPUT_DIR.is_dir():
        return None
    for path in sorted(OUTPUT_DIR.glob("benchmark_*.json")):
        if path.name.startswith("benchmark_enriched_"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("limit") != limit:
            continue
        if payload.get("query") and payload.get("query") != query:
            continue
        matches.append(path)
    return matches[0] if matches else None


def baseline_metrics(payload: dict) -> dict:
    counts = payload.get("counts") or {}
    fetching = counts.get("fetching") or {}
    ident = counts.get("identification") or {}
    discovery = counts.get("discovery") or {}
    ok = fetching.get("fetched_successfully", 0)
    fail = fetching.get("blocked_failed", 0)
    return comparison_metrics_from_counts(
        candidates=discovery.get("candidates", 0),
        raw=ident.get("raw", 0),
        deduped=ident.get("after_dedup", 0),
        ident=ident,
        total_fetches=ok + fail,
        failed_fetches=fail,
        runtime_seconds=payload.get("runtime_seconds"),
    )


def comparison_table(before: dict | None, after: dict) -> list[dict]:
    rows = []
    for key, label in COMPARISON_METRICS:
        before_value = None if before is None else before.get(key)
        after_value = after.get(key)
        change = None
        if isinstance(before_value, (int, float)) and isinstance(after_value, (int, float)):
            if key == "runtime_seconds":
                change = round(after_value - before_value, 1)
            else:
                change = after_value - before_value
        rows.append(
            {
                "metric": key,
                "label": label,
                "before": before_value,
                "after": after_value,
                "change": change,
            }
        )
    return rows


def quality_score(item: dict) -> tuple:
    gained = len(item.get("comparison", {}).get("gained") or [])
    after = item.get("after") or {}
    high = 1 if after.get("confidence") == Confidence.HIGH.value else 0
    store = 1 if after.get("physical_store") == PhysicalStore.YES.value else 0
    website = 1 if after.get("source_type") == ResultType.WEBSITE.value else 0
    return (gained, website, high, store)


def format_value(value) -> str:
    if value is None:
        return "(none)"
    text = str(value).strip()
    if not text or text.upper() == UNKNOWN:
        return "UNKNOWN"
    return text


def run_enriched_benchmark(query: str, limit: int) -> dict:
    provider = SearXNGSearchProvider.from_env()
    if provider is None:
        raise RuntimeError("SEARXNG_URL is not set. Copy .env.example to .env.")
    started = time.monotonic()
    raw_results, search_error = collect_search_results(provider, query, limit)
    if search_error and not raw_results:
        raise RuntimeError(search_error)
    candidates = candidates_from_search_results(raw_results, query)[:limit]
    fetcher = CountingFetcher(retries=0, delay_seconds=1.0)
    errors: list[dict] = []
    if search_error:
        errors.append({"stage": "search", "error": search_error})

    homepage_attempts = 0
    homepage_ok = 0
    homepage_fail = 0
    eligible = 0
    sitemap_discovered = 0
    sitemap_urls_discovered = 0
    relevant_sitemap_urls = 0
    enrichment_pages_selected = 0
    enrichment_successes = 0
    enrichment_failures = 0

    before_raw: list[BusinessCandidate] = []
    after_raw: list[BusinessCandidate] = []
    pair_records: list[dict] = []
    gain_totals = empty_gain_totals()
    compared_owners = 0

    total = len(candidates)
    for index, candidate in enumerate(candidates, start=1):
        print(
            f"[{index}/{total}] {candidate.result_type.value} {candidate.normalized_url}",
            flush=True,
        )
        enrichment_meta = {
            "eligible": False,
            "sitemap_discovered": False,
            "sitemap_source": None,
            "sitemap_url_count": 0,
            "relevant_sitemap_urls": [],
            "selected_urls": [],
            "selected_url_sources": [],
            "enrichment_successes": 0,
            "enrichment_failures": 0,
        }
        if candidate.result_type is ResultType.WEBSITE:
            eligible += 1
            homepage_attempts += 1
            enrichment_meta["eligible"] = True
            enriched = enrich_candidate(fetcher, candidate)
            enrichment_meta.update(
                {
                    "sitemap_discovered": enriched.sitemap_discovered,
                    "sitemap_source": enriched.sitemap_source,
                    "sitemap_url_count": enriched.sitemap_url_count,
                    "relevant_sitemap_urls": list(enriched.relevant_sitemap_urls),
                    "selected_urls": list(enriched.selected_urls),
                    "selected_url_sources": list(enriched.selected_url_sources),
                }
            )
            if enriched.sitemap_discovered:
                sitemap_discovered += 1
            sitemap_urls_discovered += enriched.sitemap_url_count
            relevant_sitemap_urls += len(enriched.relevant_sitemap_urls)
            enrichment_pages_selected += len(enriched.selected_urls)
            extra_ok = max(0, len(enriched.successful_urls) - (1 if enriched.pages else 0))
            extra_fail = len(
                [url for url in enriched.failed_urls if url != (candidate.normalized_url or candidate.url)]
            )
            enrichment_successes += extra_ok
            enrichment_failures += extra_fail
            enrichment_meta["enrichment_successes"] = extra_ok
            enrichment_meta["enrichment_failures"] = extra_fail
            if enriched.pages:
                homepage_ok += 1
                home = enriched.pages[0]
                home_signals = extract_business_signals(home)
                before_rows = identify_business_candidates(
                    candidate, home, home_signals
                )
                after_rows = identify_business_candidates(
                    candidate, home, home_signals, enriched=enriched
                )
            else:
                homepage_fail += 1
                errors.append(
                    {
                        "stage": "homepage",
                        "url": candidate.normalized_url,
                        "result_type": candidate.result_type.value,
                        "error": "homepage fetch failed",
                    }
                )
                empty = empty_evidence(candidate.normalized_url or candidate.url, title=candidate.title)
                before_rows = identify_business_candidates(
                    candidate, empty, empty_signals()
                )
                after_rows = list(before_rows)
        else:
            homepage_attempts += 1
            snapshot = inspect_candidate(fetcher, candidate)
            if snapshot.fetch.fetched:
                homepage_ok += 1
            else:
                homepage_fail += 1
                errors.append(
                    {
                        "stage": "fetch",
                        "url": snapshot.fetch.requested_url,
                        "result_type": candidate.result_type.value,
                        "status_code": snapshot.fetch.status_code,
                        "error": snapshot.fetch.error,
                    }
                )
            before_rows = identify_business_candidates(
                snapshot.candidate, snapshot.evidence, snapshot.signals
            )
            after_rows = list(before_rows)

        before_raw.extend(before_rows)
        after_raw.extend(after_rows)
        before_owner = primary_row(before_rows, candidate)
        after_owner = primary_row(after_rows, candidate)
        comparison = compare_snapshots(
            snapshot_business(before_owner),
            snapshot_business(after_owner),
        )
        comparable = (
            candidate.result_type is ResultType.WEBSITE
            and enrichment_meta["eligible"]
            and before_owner is not None
        )
        if comparable:
            compared_owners += 1
            add_gain(gain_totals, comparison)
        pair_records.append(
            {
                "candidate": candidate_to_dict(candidate),
                "comparable": comparable,
                "raw_before": len(before_rows),
                "raw_after": len(after_rows),
                "before": snapshot_business(before_owner),
                "after": snapshot_business(after_owner),
                "comparison": comparison,
                "enrichment": enrichment_meta,
            }
        )

    before_deduped = merge_in_memory_duplicates(before_raw)
    after_deduped = merge_in_memory_duplicates(after_raw)
    elapsed = round(time.monotonic() - started, 1)

    html_fetches = [item for item in fetcher.fetch_calls if not item["allow_xml"]]
    xml_fetches = [item for item in fetcher.fetch_calls if item["allow_xml"]]
    http_failed = [item for item in fetcher.http_requests if not item["ok"]]
    fetch_failed = [item for item in fetcher.fetch_calls if not item["fetched"]]
    kind_counts = Counter(item["kind"] for item in fetcher.http_requests)

    before_ident = identification_counts(before_deduped)
    after_ident = identification_counts(after_deduped)
    before_ident["raw"] = len(before_raw)
    after_ident["raw"] = len(after_raw)

    after_dicts = [business_to_dict(row) for row in after_deduped]
    title_by_domain = {
        item["candidate"]["domain"]: item["candidate"]["title"] for item in pair_records
    }
    enrichment_by_domain = {
        item["candidate"]["domain"]: item["enrichment"] for item in pair_records
    }
    suspicious = []
    for row in after_dicts:
        domain = extract_domain(row.get("website") or "") or extract_domain(row.get("source_url") or "") or ""
        reasons = suspicious_reasons(
            row,
            query=query,
            candidate_title=title_by_domain.get(domain, ""),
        )
        if not reasons:
            continue
        suspicious.append(
            {
                **{field: row.get(field) for field in SNAPSHOT_FIELDS},
                "reasons": reasons,
            }
        )

    gained_pairs = [item for item in pair_records if item["comparable"] and item["comparison"]["gained"]]
    quality_pool = sorted(gained_pairs, key=quality_score, reverse=True)
    if len(quality_pool) < 20:
        extras = [
            item
            for item in pair_records
            if item not in quality_pool and item["after"].get("business_name")
        ]
        extras.sort(key=quality_score, reverse=True)
        quality_pool.extend(extras)
    quality_sample = []
    for item in quality_pool[:20]:
        after = item["after"]
        quality_sample.append(
            {
                **after,
                "enrichment_pages_used": item["enrichment"].get("selected_url_sources")
                or item["enrichment"].get("selected_urls"),
                "gained": item["comparison"]["gained"],
                "lost": item["comparison"]["lost"],
                "changed": item["comparison"]["changed"],
            }
        )

    performance = {
        "runtime_seconds": elapsed,
        "homepage_fetch_count": homepage_attempts,
        "homepage_successes": homepage_ok,
        "homepage_failures": homepage_fail,
        "enrichment_fetch_count": enrichment_successes + enrichment_failures,
        "html_fetch_calls": len(html_fetches),
        "sitemap_fetch_calls": len(xml_fetches),
        "total_http_fetches": len(fetcher.http_requests),
        "failed_http": len(http_failed),
        "failed_fetch_calls": len(fetch_failed),
        "http_by_kind": dict(kind_counts),
        "robots_http": kind_counts.get("robots", 0),
        "sitemap_http": kind_counts.get("sitemap", 0),
        "html_http": kind_counts.get("html", 0),
    }
    after_metrics = comparison_metrics_from_counts(
        candidates=len(candidates),
        raw=len(after_raw),
        deduped=len(after_deduped),
        ident=after_ident,
        total_fetches=performance["total_http_fetches"],
        failed_fetches=performance["failed_http"],
        runtime_seconds=elapsed,
    )
    before_run_metrics = comparison_metrics_from_counts(
        candidates=len(candidates),
        raw=len(before_raw),
        deduped=len(before_deduped),
        ident=before_ident,
        total_fetches=homepage_attempts,
        failed_fetches=homepage_fail,
        runtime_seconds=None,
    )

    report = {
        "query": query,
        "limit": limit,
        "runtime_seconds": elapsed,
        "counts": {
            "search": {"raw_search_results": len(raw_results)},
            "discovery": {
                "candidates": len(candidates),
                "by_type": _result_type_counts(candidates),
            },
            "fetching": {
                "homepage_attempts": homepage_attempts,
                "homepage_successes": homepage_ok,
                "homepage_failures": homepage_fail,
                "fetched_successfully": homepage_ok,
                "blocked_failed": homepage_fail,
            },
            "enrichment": {
                "candidates_eligible": eligible,
                "sitemap_discovered": sitemap_discovered,
                "sitemap_urls_discovered": sitemap_urls_discovered,
                "relevant_sitemap_urls": relevant_sitemap_urls,
                "enrichment_pages_selected": enrichment_pages_selected,
                "enrichment_successes": enrichment_successes,
                "enrichment_failures": enrichment_failures,
            },
            "identification": after_ident,
            "identification_before_enrichment": before_ident,
        },
        "information_gain": {
            "comparable_website_owners": compared_owners,
            **gain_totals,
            "worse_owner_rows": sum(
                1
                for item in pair_records
                if item["comparable"] and item["comparison"]["worse"]
            ),
            "changed_owner_rows": sum(
                1
                for item in pair_records
                if item["comparable"] and item["comparison"]["changed"]
            ),
            "gained_any_field": sum(
                1
                for item in pair_records
                if item["comparable"] and item["comparison"]["gained"]
            ),
        },
        "performance": performance,
        "same_run_homepage_vs_enriched": comparison_table(
            before_run_metrics, after_metrics
        ),
        "candidates": [candidate_to_dict(row) for row in candidates],
        "business_candidates": after_dicts,
        "business_candidates_before": [business_to_dict(row) for row in before_deduped],
        "pair_records": pair_records,
        "suspicious": suspicious,
        "quality_sample": quality_sample,
        "errors": errors,
        "_after_metrics": after_metrics,
        "enrichment_by_domain": enrichment_by_domain,
    }
    return report


def attach_baseline_comparison(report: dict, baseline_path: Path | None) -> None:
    after_metrics = report.pop("_after_metrics")
    if baseline_path is None:
        report["baseline"] = None
        report["comparison"] = comparison_table(None, after_metrics)
        return
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    before_metrics = baseline_metrics(payload)
    report["baseline"] = {
        "path": str(baseline_path),
        "query": payload.get("query"),
        "limit": payload.get("limit"),
        "metrics": before_metrics,
    }
    report["comparison"] = comparison_table(before_metrics, after_metrics)


def write_json(report: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"benchmark_enriched_{stamp}.json"
    if path.exists():
        path = OUTPUT_DIR / f"benchmark_enriched_{stamp}_limit{report['limit']}.json"
    serializable = {
        key: value
        for key, value in report.items()
        if not key.startswith("_")
    }
    path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    return path


def _print_count_block(title: str, mapping: dict) -> None:
    print(title)
    for key, value in mapping.items():
        print(f"{key}: {value}")
    print()


def print_summary(report: dict) -> None:
    counts = report["counts"]
    search = counts["search"]
    discovery = counts["discovery"]
    fetching = counts["fetching"]
    enrichment = counts["enrichment"]
    ident = counts["identification"]
    gain = report["information_gain"]
    performance = report["performance"]

    print("=== ENRICHMENT IMPACT BENCHMARK ===")
    print(f"Query: {report['query']}")
    print(f"Limit: {report['limit']}")
    print(f"Runtime: {performance['runtime_seconds']}s")
    print()
    print("SEARCH")
    print(f"Raw search results: {search['raw_search_results']}")
    print()
    print("DISCOVERY")
    print(f"Candidates: {discovery['candidates']}")
    _print_count_block("Candidates by type", discovery["by_type"])
    print("FETCHING")
    print(f"Homepage attempts: {fetching['homepage_attempts']}")
    print(f"Homepage successes: {fetching['homepage_successes']}")
    print(f"Homepage failures: {fetching['homepage_failures']}")
    print()
    print("ENRICHMENT")
    print(f"Candidates eligible for enrichment: {enrichment['candidates_eligible']}")
    print(f"Sitemap discovered: {enrichment['sitemap_discovered']}")
    print(f"Sitemap URLs discovered: {enrichment['sitemap_urls_discovered']}")
    print(f"Relevant sitemap URLs: {enrichment['relevant_sitemap_urls']}")
    print(f"Enrichment pages selected: {enrichment['enrichment_pages_selected']}")
    print(f"Enrichment successes: {enrichment['enrichment_successes']}")
    print(f"Enrichment failures: {enrichment['enrichment_failures']}")
    print()
    print("IDENTIFICATION")
    print(f"Raw BusinessCandidates: {ident['raw']}")
    print(f"After deduplication: {ident['after_dedup']}")
    print()
    _print_count_block("business_type", ident["business_type"])
    _print_count_block("women_fashion_relevance", ident["women_fashion"])
    _print_count_block("physical_store", ident["physical_store"])
    _print_count_block("confidence", ident["confidence"])
    _print_count_block("contact availability", ident["contact"])

    print("INFORMATION GAIN (same candidates, homepage vs enrichment)")
    print(f"Comparable website owners: {gain['comparable_website_owners']}")
    print(f"Owners that gained any field: {gain['gained_any_field']}")
    print(f"Owners with a worse field: {gain['worse_owner_rows']}")
    print(f"Owners with a changed field: {gain['changed_owner_rows']}")
    print()
    print(f"{'Field':<28} {'Gained':>8} {'Lost':>8} {'Changed':>8} {'Worse':>8}")
    keys = list(GAIN_FIELDS) + ["high_confidence"]
    for field in keys:
        print(
            f"{field:<28} {gain['gained'][field]:>8} {gain['lost'][field]:>8} "
            f"{gain['changed'][field]:>8} {gain['worse'].get(field, 0):>8}"
        )
    print()
    print("FALSE POSITIVES / SUSPICIOUS (heuristic flags only)")
    suspicious = report["suspicious"]
    print(f"Flagged records: {len(suspicious)}")
    if not suspicious:
        print("(none)")
    for index, row in enumerate(suspicious[:20], start=1):
        print(f"{index}. {format_value(row.get('business_name'))}")
        print(f"   reasons={', '.join(row['reasons'])}")
        print(f"   type={row.get('business_type')} city={format_value(row.get('city'))}")
        print(f"   website={format_value(row.get('website'))}")
        print(f"   source={format_value(row.get('source_url'))}")
    if len(suspicious) > 20:
        print(f"... {len(suspicious) - 20} more flagged records in JSON")
    print()
    print("QUALITY SAMPLE (up to 20; prefers information gained)")
    sample = report["quality_sample"]
    if not sample:
        print("(none)")
    for index, row in enumerate(sample, start=1):
        pages = row.get("enrichment_pages_used") or []
        page_text = ", ".join(
            item["url"] if isinstance(item, dict) else str(item) for item in pages
        ) or "(none)"
        print(f"{index}. {format_value(row.get('business_name'))}")
        print(f"   type={row.get('business_type')} women={row.get('women_fashion_relevance')}")
        print(f"   store={row.get('physical_store')} city={format_value(row.get('city'))}")
        print(f"   address={format_value(row.get('address'))}")
        print(f"   website={format_value(row.get('website'))}")
        print(f"   instagram={format_value(row.get('instagram'))}")
        print(f"   phone={format_value(row.get('phone'))} email={format_value(row.get('email'))}")
        print(f"   confidence={row.get('confidence')} source_type={row.get('source_type')}")
        print(f"   enrichment pages={page_text}")
        print(
            f"   gained={row.get('gained') or []} lost={row.get('lost') or []} "
            f"changed={row.get('changed') or []}"
        )
    print()
    print("PERFORMANCE")
    print(f"Total runtime: {performance['runtime_seconds']}s")
    print(f"Homepage fetch count: {performance['homepage_fetch_count']}")
    print(f"Enrichment fetch count: {performance['enrichment_fetch_count']}")
    print(f"HTML fetch calls: {performance['html_fetch_calls']}")
    print(f"Sitemap fetch calls: {performance['sitemap_fetch_calls']}")
    print(f"Total HTTP fetches: {performance['total_http_fetches']}")
    print(f"Failed HTTP: {performance['failed_http']}")
    print(f"HTTP by kind: {performance['http_by_kind']}")
    print()
    print("COMPARISON vs Step 6.5 (homepage-only saved benchmark)")
    baseline = report.get("baseline")
    if baseline:
        print(f"Baseline file: {baseline['path']}")
    else:
        print("Baseline file: (none found)")
    print()
    print(f"{'Metric':<34} {'Before':>10} {'After':>10} {'Change':>10}")
    for row in report.get("comparison") or []:
        before = "-" if row["before"] is None else row["before"]
        after = "-" if row["after"] is None else row["after"]
        change = "-" if row["change"] is None else row["change"]
        print(f"{row['label']:<34} {str(before):>10} {str(after):>10} {str(change):>10}")
    print()
    print(
        "Increases are not automatically better. This table measures recall, "
        "populated fields, request volume, and runtime together."
    )
    print()
    print("SAME-RUN homepage-only vs enriched (identical candidates/fetches)")
    print(f"{'Metric':<34} {'Before':>10} {'After':>10} {'Change':>10}")
    for row in report.get("same_run_homepage_vs_enriched") or []:
        before = "-" if row["before"] is None else row["before"]
        after = "-" if row["after"] is None else row["after"]
        change = "-" if row["change"] is None else row["change"]
        print(f"{row['label']:<34} {str(before):>10} {str(after):>10} {str(change):>10}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        limits = parse_limits(args.limit, args.limits)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    query = " ".join(args.query).strip() or DEFAULT_QUERY
    written: list[Path] = []
    for limit in limits:
        print(f"\n######## ENRICHED RUN limit={limit} ########\n", flush=True)
        try:
            report = run_enriched_benchmark(query, limit)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        baseline = find_baseline(limit, query, args.baseline)
        attach_baseline_comparison(report, baseline)
        print()
        print_summary(report)
        if not args.no_json:
            path = write_json(report)
            written.append(path)
            print()
            print(f"JSON report: {path}")
    if len(written) > 1:
        print()
        print("Wrote:")
        for path in written:
            print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
