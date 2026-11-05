"""Small multi-city stockist benchmark.

Uses the existing SearXNG provider, candidate dedup, page fetch, article
expansion, and stockist-fit scoring. Local Qwen stays off.

    python src/fit_benchmark.py
    python src/fit_benchmark.py --per-family 1 --limit 2
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from batch_benchmark import run_batch_benchmark, write_batch_json
from search_queries import sample_batch

CITIES = ("Goa", "Mumbai", "Delhi", "Bengaluru", "Jaipur")


def _fit(row: dict) -> dict:
    evidence = row.get("evidence") or {}
    fit = evidence.get("fit")
    return fit if isinstance(fit, dict) else {}


def _city(row: dict) -> str:
    evidence = row.get("evidence") or {}
    return (
        evidence.get("expected_city")
        or row.get("city")
        or "Unknown"
    )


def _from_listing(row: dict) -> bool:
    evidence = row.get("evidence") or {}
    kind = str(evidence.get("discovered_from") or evidence.get("discovery_source_type") or "")
    if kind in {"article", "directory"}:
        return True
    signals = set(evidence.get("signals") or [])
    return bool(signals & {"article_business_link", "article_heading", "article_mention", "external_business_link"})


def summarize(report: dict) -> dict:
    specs = report.get("queries") or []
    candidates = report.get("candidates") or []
    rows = report.get("business_candidates_llm") or report.get("business_candidates_rules") or []
    by_city: dict[str, dict] = {}
    for city in CITIES:
        city_specs = [item for item in specs if item.get("city") == city]
        city_candidates = [
            item
            for item in candidates
            if city.casefold() in " ".join(item.get("discovered_by_queries") or []).casefold()
            or city.casefold() in str(item.get("search_query") or "").casefold()
        ]
        city_rows = [row for row in rows if str(_city(row)).casefold() == city.casefold()]
        if not city_rows:
            city_rows = [
                row
                for row in rows
                if city.casefold() in " ".join((row.get("evidence") or {}).get("discovered_by_queries") or []).casefold()
            ]
        statuses = defaultdict(int)
        for row in city_rows:
            statuses[str(_fit(row).get("status") or "UNSCORED")] += 1
        by_city[city] = {
            "city": city,
            "queries": len(city_specs),
            "raw_search_results": sum(int(item.get("raw_results") or 0) for item in report.get("query_metrics") or [] if item.get("query") in {spec["query"] for spec in city_specs}),
            "business_candidates": len(city_rows),
            "article_directory_pages": sum(
                1 for item in city_candidates if item.get("result_type") in {"ARTICLE", "DIRECTORY"}
            ),
            "businesses_extracted_from_articles": sum(1 for row in city_rows if _from_listing(row)),
            "physical_stores_verified": sum(1 for row in city_rows if _fit(row).get("physical_store") == "YES"),
            "fashion_relevant_businesses": sum(
                1 for row in city_rows if _fit(row).get("fashion_relevance") in {"HIGH", "MEDIUM"}
            ),
            "multi_brand_curated_businesses": sum(
                1
                for row in city_rows
                if _fit(row).get("multi_brand") == "YES" or int(_fit(row).get("concept_store_score") or 0) >= 3
            ),
            "LEAD": statuses["LEAD"],
            "REVIEW": statuses["REVIEW"],
            "EXCLUDED": statuses["EXCLUDED"],
        }
    ranked = sorted(
        rows,
        key=lambda row: (
            {"LEAD": 0, "REVIEW": 1, "EXCLUDED": 2}.get(str(_fit(row).get("status")), 3),
            -int(_fit(row).get("anazvara_fit_score") or 0),
            str(row.get("business_name") or ""),
        ),
    )
    top = []
    for row in ranked:
        if str(row.get("business_name") or "") in {"", "UNKNOWN"}:
            continue
        fit = _fit(row)
        if fit.get("status") == "EXCLUDED" and len([item for item in top if item["status"] != "EXCLUDED"]) >= 20:
            continue
        top.append(
            {
                "business_name": row.get("business_name"),
                "city": _city(row),
                "website": row.get("website"),
                "physical_store_evidence": fit.get("evidence_physical_store") or [],
                "retail_model": fit.get("retail_model"),
                "anazvara_fit": fit.get("anazvara_fit"),
                "status": fit.get("status"),
                "reason": fit.get("reason"),
            }
        )
        if len(top) >= 20:
            break
    return {"by_city": [by_city[city] for city in CITIES], "top_businesses": top}


def print_summary(summary: dict) -> None:
    print()
    print("city | queries | raw | candidates | articles | extracted | physical | fashion | curated | LEAD | REVIEW | EXCLUDED")
    for row in summary["by_city"]:
        print(
            " | ".join(
                str(row[key])
                for key in (
                    "city",
                    "queries",
                    "raw_search_results",
                    "business_candidates",
                    "article_directory_pages",
                    "businesses_extracted_from_articles",
                    "physical_stores_verified",
                    "fashion_relevant_businesses",
                    "multi_brand_curated_businesses",
                    "LEAD",
                    "REVIEW",
                    "EXCLUDED",
                )
            )
        )
    print()
    print("Top discovered businesses")
    for index, row in enumerate(summary["top_businesses"], start=1):
        evidence = ", ".join(row["physical_store_evidence"]) or "none"
        print(
            f"{index}. {row['business_name']} | {row['city']} | {row['website'] or '-'} | "
            f"physical: {evidence} | model: {row['retail_model']} | "
            f"fit: {row['anazvara_fit']} | {row['status']} | {row['reason']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Small multi-city stockist benchmark")
    parser.add_argument("--per-family", type=int, default=1)
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--no-json", action="store_true")
    args = parser.parse_args(argv)
    specs = sample_batch(CITIES, per_family=args.per_family, limit=args.limit)
    report = run_batch_benchmark(specs, enable_llm=False)
    summary = summarize(report)
    report["fit_summary"] = summary
    output = "(skipped)"
    if not args.no_json:
        output = str(write_batch_json(report, output_dir=PROJECT_ROOT / "output"))
        summary_path = Path(output).with_name(Path(output).stem + "_fit_summary.json")
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        output = f"{output} | {summary_path}"
    print_summary(summary)
    print()
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
