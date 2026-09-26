"""Goa multi-query stockist benchmark.

Uses the existing search → fetch → rules → Qwen path.
Does not change production classification or hard-code businesses there.

Villa Mor is included via extra discovery queries (and a benchmark-only
fallback seed if those queries miss it). The seed is not used by local_llm.py.
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

from benchmark import OUTPUT_DIR, collect_search_results
from benchmark_seeds import (
    GOA_STOCKIST_SEEDS,
    classify_resolved_seed,
    resolve_seed,
    seed_metrics,
    seed_report_row,
)
from business_candidates import (
    UNKNOWN,
    BusinessCandidate,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
)
from candidates import (
    Candidate,
    _dedupe_key,
    _prefer_candidate,
    candidate_from_search_result,
    candidates_from_search_results,
)
from classification import ResultType
from enrichment_benchmark import CountingFetcher
from llm_benchmark import (
    evaluate_candidates,
    match_stockist_references,
    print_summary,
    stockist_of,
)
from local_llm import LocalLLMClient
from search_provider import SearchResult
from searxng_provider import SearXNGSearchProvider

GOA_QUERIES = (
    "women's fashion boutiques Goa",
    "multi brand boutiques Goa",
    "designer boutiques Goa",
    "fashion stores Goa",
    "multi designer stores Goa",
    "fashion boutiques Panjim",
    "fashion boutiques Anjuna",
    "fashion boutiques Assagao",
    "fashion boutiques Vagator",
    "fashion boutiques Morjim",
)
# Extra searches so the reference case can enter the same pipeline.
# Not a production business list.
VILLA_MOR_QUERIES = (
    "Villa Mor boutique Goa",
    'Villa Mor "Village Shop" Goa',
)
# Used only if the extra searches do not return a matching hit.
VILLA_MOR_FALLBACK = SearchResult(
    title="Villa Mor (@villamor_shop)",
    url="https://www.instagram.com/villamor_shop/",
    snippet="Villa Mor shop Instagram",
    source="benchmark_reference",
)
BUCKETS = (
    "A_multi_brand_or_curated_stockist",
    "B_own_label_designer_or_brand",
    "C_generic_clothing_retailer",
    "D_directory",
    "E_article_or_editorial",
    "F_other_or_unrelated",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Goa stockist discovery benchmark.")
    parser.add_argument("--limit", type=int, default=8, help="Max candidates per query")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--no-json", action="store_true")
    parser.add_argument(
        "--full-discovery",
        action="store_true",
        help="Also run the full organic fetch+Qwen discovery pass",
    )
    parser.add_argument("--skip-seeds", action="store_true")
    return parser


def merge_candidates(existing: list[Candidate], incoming: list[Candidate]) -> list[Candidate]:
    grouped: dict[tuple[str, str], Candidate] = {}
    order: list[tuple[str, str]] = []
    for candidate in [*existing, *incoming]:
        key = _dedupe_key(candidate)
        if key not in grouped:
            grouped[key] = candidate
            order.append(key)
        else:
            grouped[key] = _prefer_candidate(grouped[key], candidate)
    return [grouped[key] for key in order]


def looks_like_villa_mor(candidate: Candidate) -> bool:
    hay = " ".join(
        part for part in (candidate.title, candidate.url, candidate.snippet) if part
    ).lower()
    return "villa mor" in hay or "villamor" in hay


def reporting_bucket(row: BusinessCandidate) -> str:
    """Benchmark-only grouping. Does not change stored classification."""
    signals = list((row.evidence or {}).get("signals") or [])
    if row.source_type == ResultType.DIRECTORY.value:
        return "D_directory"
    if row.source_type == ResultType.ARTICLE.value or any(
        item in signals for item in ("roundup_page", "publisher_not_boutique")
    ):
        return "E_article_or_editorial"
    stockist = stockist_of(row, after_ai=True)
    ai_type = str((row.evidence or {}).get("ai_business_type") or row.business_type.value)
    positioning = str(
        ((row.evidence or {}).get("ai") or {}).get("validated", {}).get("designer_positioning")
        or ""
    )
    if stockist == "YES":
        return "A_multi_brand_or_curated_stockist"
    if (
        row.business_type in {BusinessType.DESIGNER, BusinessType.BRAND}
        or ai_type in {"DESIGNER", "OWN_BRAND", "BRAND"}
        or positioning == "OWN_LABEL"
    ):
        return "B_own_label_designer_or_brand"
    if row.business_type in {BusinessType.BOUTIQUE, BusinessType.RETAILER, BusinessType.MULTI_DESIGNER}:
        return "C_generic_clothing_retailer"
    if row.source_type in {ResultType.SOCIAL.value, ResultType.VIDEO.value}:
        return "F_other_or_unrelated"
    return "F_other_or_unrelated"


def _multi_brand_evidence(row: BusinessCandidate) -> list[str]:
    validated = ((row.evidence or {}).get("ai") or {}).get("validated") or {}
    items = [str(item) for item in (validated.get("evidence") or [])]
    return items[:6]


def inspect_rows(rows: list[BusinessCandidate]) -> dict:
    yes: list[dict] = []
    fashion_no: list[dict] = []
    unknown: list[dict] = []
    for row in rows:
        stockist = stockist_of(row, after_ai=True)
        women = row.women_fashion_relevance.value
        typed = row.business_type is not BusinessType.UNKNOWN
        item = {
            "business": row.business_name,
            "url": row.website or row.source_url,
            "city": row.city,
            "source_type": row.source_type,
            "business_type": row.business_type.value,
            "women_fashion": women,
            "potential_stockist": stockist,
            "confidence": row.confidence.value,
            "ai_confidence": (row.evidence or {}).get("ai_confidence"),
            "evidence": _multi_brand_evidence(row),
            "rejected": ((row.evidence or {}).get("ai") or {})
            .get("validated", {})
            .get("rejected")
            or [],
            "bucket": reporting_bucket(row),
        }
        if stockist == "YES":
            yes.append(item)
        elif stockist == "NO" and (women in {"HIGH", "MEDIUM"} or typed):
            item["why_not_stockist"] = item["rejected"] or item["evidence"][:2] or [
                "classified NO without a stockist-positive page signal"
            ]
            fashion_no.append(item)
        elif stockist == "UNKNOWN" and row.source_type == ResultType.WEBSITE.value:
            missing = []
            if not item["evidence"]:
                missing.append("no validated AI evidence list")
            if row.business_type is BusinessType.UNKNOWN:
                missing.append("business_type UNKNOWN")
            if women == "UNKNOWN":
                missing.append("women_fashion UNKNOWN")
            if not missing:
                missing.append("page lacked multi-brand/curated language")
            item["missing_evidence"] = missing
            unknown.append(item)
    return {"yes": yes, "fashion_no": fashion_no, "unknown": unknown}


def discover_goa_candidates(provider: SearXNGSearchProvider, limit: int) -> tuple[list[Candidate], dict]:
    errors: list[dict] = []
    all_raw = 0
    per_query: list[dict] = []
    merged: list[Candidate] = []
    villa_hits = 0
    for query in GOA_QUERIES:
        raw, search_error = collect_search_results(provider, query, limit)
        if search_error:
            errors.append({"stage": "search", "query": query, "error": search_error})
        batch = candidates_from_search_results(raw, query)[:limit]
        all_raw += len(raw)
        villa_hits += sum(1 for item in batch if looks_like_villa_mor(item))
        merged = merge_candidates(merged, batch)
        per_query.append({"query": query, "raw": len(raw), "candidates": len(batch)})
        print(f"search {query!r}: {len(batch)} candidates", flush=True)

    for query in VILLA_MOR_QUERIES:
        raw, search_error = collect_search_results(provider, query, limit)
        if search_error:
            errors.append({"stage": "search", "query": query, "error": search_error})
        batch = candidates_from_search_results(raw, query)
        villa_batch = [item for item in batch if looks_like_villa_mor(item)]
        all_raw += len(raw)
        villa_hits += len(villa_batch)
        merged = merge_candidates(merged, villa_batch)
        per_query.append(
            {
                "query": query,
                "raw": len(raw),
                "candidates": len(villa_batch),
                "reference_filter": True,
            }
        )
        print(f"reference search {query!r}: {len(villa_batch)} Villa Mor hits", flush=True)

    if not any(looks_like_villa_mor(item) for item in merged):
        seeded = candidate_from_search_result(VILLA_MOR_FALLBACK, "Villa Mor boutique Goa")
        if seeded is not None:
            merged = merge_candidates(merged, [seeded])
            print("Villa Mor not in discovery; seeded Instagram profile for evaluation only", flush=True)
            per_query.append(
                {
                    "query": "benchmark_reference_seed",
                    "raw": 1,
                    "candidates": 1,
                    "reference_filter": True,
                }
            )
    return merged, {
        "errors": errors,
        "raw_search_results": all_raw,
        "per_query": per_query,
        "villa_mor_search_hits": villa_hits,
        "villa_mor_in_candidates": sum(1 for item in merged if looks_like_villa_mor(item)),
    }


def print_inspection(report: dict) -> None:
    inspection = report["inspection"]
    print("=== POTENTIAL YES CANDIDATES ===")
    if not inspection["yes"]:
        print("(none)")
    for item in inspection["yes"]:
        print(f"- {item['business']} | {item['url']}")
        print(
            f"  city={item['city']} type={item['business_type']} "
            f"stockist={item['potential_stockist']} confidence={item['confidence']}"
        )
        print(f"  evidence={item['evidence']}")
    print()
    print("=== FASHION BUSINESSES THAT ARE NOT STOCKIST PROSPECTS ===")
    if not inspection["fashion_no"]:
        print("(none)")
    for item in inspection["fashion_no"]:
        print(f"- {item['business']} | {item['business_type']} | {item['url']}")
        print(f"  why: {item['why_not_stockist']}")
    print()
    print("=== UNKNOWN WEBSITE CANDIDATES ===")
    if not inspection["unknown"]:
        print("(none)")
    for item in inspection["unknown"]:
        print(f"- {item['business']} | {item['url']}")
        print(f"  missing: {item['missing_evidence']}")
    print()
    print("=== REPORTING BUCKETS ===")
    for key in BUCKETS:
        print(f"{key}: {report['buckets'][key]}")
    print()


def run_goa_benchmark(*, limit: int, enable_llm: bool) -> dict:
    provider = SearXNGSearchProvider.from_env()
    if provider is None:
        raise RuntimeError("SEARXNG_URL is not set. Copy .env.example to .env.")
    started = time.monotonic()
    candidates, discovery = discover_goa_candidates(provider, limit)
    report = evaluate_candidates(
        candidates,
        enable_llm=enable_llm,
        query_label="Goa stockist discovery",
        extra_errors=discovery["errors"],
        raw_search_count=discovery["raw_search_results"],
        limit=limit,
        started=started,
    )
    reconstructed = [
        row_from_dict(item) for item in report["business_candidates_llm"]
    ]
    bucket_counts = {key: 0 for key in BUCKETS}
    for row in reconstructed:
        bucket_counts[reporting_bucket(row)] += 1

    report["goa"] = {
        "queries": list(GOA_QUERIES),
        "reference_queries": list(VILLA_MOR_QUERIES),
        "per_query": discovery["per_query"],
        "villa_mor_search_hits": discovery["villa_mor_search_hits"],
        "villa_mor_in_candidates": discovery["villa_mor_in_candidates"],
    }
    report["buckets"] = bucket_counts
    report["inspection"] = inspect_rows(reconstructed)
    report["stockist"]["reference_examples"] = match_stockist_references(reconstructed)
    return report


def row_from_dict(item: dict) -> BusinessCandidate:
    return BusinessCandidate(
        business_name=item["business_name"],
        website=item.get("website"),
        instagram=item.get("instagram"),
        facebook=item.get("facebook"),
        whatsapp=item.get("whatsapp"),
        phone=item.get("phone"),
        email=item.get("email"),
        address=item.get("address"),
        city=item.get("city") or UNKNOWN,
        source_url=item.get("source_url") or "",
        source_type=item.get("source_type") or "UNKNOWN",
        business_type=BusinessType(item.get("business_type") or "UNKNOWN"),
        fashion_relevance=Relevance(item.get("fashion_relevance") or "UNKNOWN"),
        women_fashion_relevance=Relevance(
            item.get("women_fashion_relevance") or "UNKNOWN"
        ),
        physical_store=PhysicalStore(item.get("physical_store") or "UNKNOWN"),
        evidence=item.get("evidence") or {},
        confidence=Confidence(item.get("confidence") or "LOW"),
    )


def collect_organic_candidates(provider: SearXNGSearchProvider, limit: int) -> list[Candidate]:
    merged: list[Candidate] = []
    for query in GOA_QUERIES:
        raw, _error = collect_search_results(provider, query, limit)
        batch = candidates_from_search_results(raw, query)[:limit]
        merged = merge_candidates(merged, batch)
        print(f"organic search {query!r}: {len(batch)} candidates", flush=True)
    return merged


def run_seed_benchmark(
    *,
    enable_llm: bool,
    organic_candidates: list[Candidate],
) -> dict:
    fetcher = CountingFetcher(retries=0, delay_seconds=1.0)
    client = LocalLLMClient(enabled=enable_llm)
    provider = SearXNGSearchProvider.from_env()
    if provider is None:
        raise RuntimeError("SEARXNG_URL is not set. Copy .env.example to .env.")
    seed_rows: list[dict] = []
    for seed in GOA_STOCKIST_SEEDS:
        print(f"seed resolve {seed.seed_name}", flush=True)
        resolution = resolve_seed(
            seed, provider, fetcher, organic_candidates=organic_candidates
        )
        rows, stats = classify_resolved_seed(
            fetcher=fetcher, client=client, resolution=resolution
        )
        report_row = seed_report_row(resolution, rows, stats)
        seed_rows.append(report_row)
        print(
            f"  identity={report_row['identity_type'] or '—'} "
            f"verified={report_row['identity_verified']} "
            f"from={report_row['entity_verified_from'] or '—'} "
            f"stockist={report_row['potential_stockist']}",
            flush=True,
        )
    return {"seeds": seed_rows, "seed_metrics": seed_metrics(seed_rows)}


def print_seed_report(block: dict) -> None:
    print("=== SEED DISCOVERY ===")
    metrics = block["seed_metrics"]
    for key, value in metrics.items():
        print(f"{key}: {value}")
    print()
    print(
        f"{'Seed':<24} {'ID':<4} {'Type':<12} {'Verified':<8} "
        f"{'Stockist':<9} {'Validation':<10} Primary"
    )
    for row in block["seeds"]:
        primary = (
            row.get("website")
            or row.get("instagram")
            or row.get("facebook")
            or row.get("google_maps_url")
            or "—"
        )
        print(
            f"{row['seed_name']:<24} "
            f"{'yes' if row['identity_found'] else 'no':<4} "
            f"{(row['identity_type'] or '—'):<12} "
            f"{'yes' if row['identity_verified'] else 'no':<8} "
            f"{row['potential_stockist']:<9} "
            f"{row['validation']:<10} "
            f"{primary}"
        )
        print(
            f"  website={row.get('website') or '—'} "
            f"instagram={row.get('instagram') or '—'} "
            f"facebook={row.get('facebook') or '—'} "
            f"maps={'yes' if row.get('google_business_verified') else 'no'}"
        )
        print(
            f"  extracted={row['business_identity']} type={row['business_type_rules']}/"
            f"{row['business_type_qwen']} women={row['women_fashion_rules']}/"
            f"{row['women_fashion_qwen']} carries={row['carries_other_brands']} "
            f"store={row['physical_store']} stockist_evidence="
            f"{'yes' if row.get('stockist_evidence_available') else 'no'}"
        )
        print(
            f"  origin={row['entity_origin']} verified_from="
            f"{row.get('entity_verified_from') or '—'} "
            f"relationship={row.get('entity_relationship')}"
        )
        if row.get("validation_reasons"):
            print(f"  validation_reasons={row['validation_reasons']}")
        if row["evidence"]:
            print(f"  evidence={row['evidence']}")
        if row["rejection_reason"]:
            print(f"  rejected={row['rejection_reason']}")
        if row["stockist_pages"]:
            print(f"  extra pages={row['stockist_pages']}")
    print()


def write_json(report: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"benchmark_goa_{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 1:
        print("--limit must be at least 1", file=sys.stderr)
        return 1
    try:
        provider = SearXNGSearchProvider.from_env()
        if provider is None:
            raise RuntimeError("SEARXNG_URL is not set. Copy .env.example to .env.")
        print("=== NORMAL DISCOVERY (search only) ===", flush=True)
        organic = collect_organic_candidates(provider, args.limit)
        print(f"Organic candidates after merge: {len(organic)}", flush=True)
        report: dict = {
            "query": "Goa seed + organic comparison",
            "limit": args.limit,
            "organic_discovery": {
                "candidates": len(organic),
                "urls": [item.normalized_url for item in organic],
            },
        }
        if not args.skip_seeds:
            report.update(run_seed_benchmark(enable_llm=not args.no_llm, organic_candidates=organic))
        if args.full_discovery:
            report["full_discovery"] = run_goa_benchmark(
                limit=args.limit, enable_llm=not args.no_llm
            )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print()
    print("NORMAL DISCOVERY")
    print(f"Candidates found organically: {report['organic_discovery']['candidates']}")
    if report.get("seed_metrics"):
        print(
            f"Seeds also found in organic search: {report['seed_metrics']['seeds_organic_hits']}/"
            f"{report['seed_metrics']['seeds_total']}"
        )
        print()
        print_seed_report(report)
    if report.get("full_discovery"):
        print_summary(report["full_discovery"])
        print_inspection(report["full_discovery"])
    if not args.no_json:
        path = write_json(report)
        print()
        print(f"JSON report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
