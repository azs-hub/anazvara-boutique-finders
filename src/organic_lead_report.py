"""Benchmark-only organic discovery funnel.

Does not change classification, Qwen, identity resolution, or enrichment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from business_candidates import UNKNOWN, BusinessCandidate, BusinessType
from classification import ResultType
from stockist_lead import contact_paths, lead_of

LISTING_SOURCES = {ResultType.DIRECTORY.value, ResultType.ARTICLE.value, ResultType.VIDEO.value}
LISTING_SIGNALS = {"roundup_page", "publisher_not_boutique"}
CONTACT_KEYS = ("website", "instagram", "facebook", "email", "phone")
IDENTITY_CONTACT_KEYS = ("website", "instagram", "facebook", "google_maps_url")
YES_NO_UNKNOWN = {"YES", "NO", "UNKNOWN"}


def _has(value: Any) -> bool:
    return bool(value) and value != UNKNOWN


def _stockist(row: BusinessCandidate) -> str:
    value = str((row.evidence or {}).get("ai_potential_stockist") or "UNKNOWN").upper()
    return value if value in YES_NO_UNKNOWN else "UNKNOWN"


def _reasons(row: BusinessCandidate) -> list[str]:
    return [str(item) for item in (row.evidence or {}).get("stockist_lead_reasons") or []]


def _signals(row: BusinessCandidate) -> list[str]:
    return list((row.evidence or {}).get("signals") or [])


def _entity_field(row: BusinessCandidate, key: str, default: str | None = None) -> str | None:
    value = (row.evidence or {}).get(key)
    return str(value) if value not in {None, ""} else default


def is_listing_page(row: BusinessCandidate) -> bool:
    relationship = _entity_field(row, "entity_relationship")
    if relationship in {"ARTICLE", "MEDIA", "DIRECTORY"}:
        return True
    if row.source_type in LISTING_SOURCES:
        return True
    return any(item in LISTING_SIGNALS for item in _signals(row))


def identity_found(row: BusinessCandidate) -> bool:
    if _entity_field(row, "entity_is_business") == "YES" and _entity_field(
        row, "entity_relationship"
    ) == "SELF":
        return True
    return _has(row.business_name) and not is_listing_page(row)


def has_verified_identity(row: BusinessCandidate) -> bool:
    if not identity_found(row):
        return False
    paths = contact_paths(row)
    return any(_has(paths.get(key)) for key in IDENTITY_CONTACT_KEYS)


def identity_verified_from(row: BusinessCandidate) -> str | None:
    stored = (row.evidence or {}).get("entity_verified_from")
    if stored:
        return str(stored)
    parts: list[str] = []
    if _has(row.website) and row.source_type == ResultType.WEBSITE.value:
        parts.append("OFFICIAL_WEBSITE")
    elif _has(row.website):
        parts.append("WEBSITE")
    if _has(row.instagram):
        parts.append("OFFICIAL_INSTAGRAM")
    if _has(row.facebook):
        parts.append("OFFICIAL_FACEBOOK")
    maps = (row.evidence or {}).get("google_maps_url")
    if _has(maps):
        parts.append("GOOGLE_BUSINESS")
    return " + ".join(parts) if parts else None


def is_valid_business_entity(row: BusinessCandidate) -> bool:
    if _entity_field(row, "entity_relationship") == "SELF":
        return _entity_field(row, "entity_is_business") == "YES"
    return identity_found(row) and _entity_field(row, "entity_is_business") != "NO"


def fashion_retail_relevant(row: BusinessCandidate) -> bool:
    reasons = _reasons(row)
    if lead_of(row) == "YES":
        return True
    if "fashion_retail_page_evidence" in reasons:
        return True
    if lead_of(row) == "UNKNOWN" and reasons == ["no_usable_contact_path"]:
        return True
    ai_type = str((row.evidence or {}).get("ai_business_type") or "").upper()
    return row.business_type.value in {
        "BOUTIQUE",
        "MULTI_DESIGNER",
        "MULTI_BRAND",
        "CONCEPT_STORE",
        "RETAILER",
        "FASHION_RETAILER",
    } or ai_type in {
        "BOUTIQUE",
        "MULTI_DESIGNER",
        "MULTI_BRAND",
        "CONCEPT_STORE",
        "RETAILER",
        "FASHION_RETAILER",
    }


def failure_class(row: BusinessCandidate) -> str | None:
    """Separate discovery failure from classification failure."""
    if is_listing_page(row):
        if _has(row.business_name) and row.business_type is not BusinessType.UNKNOWN:
            return "C_listing_mistaken_for_business"
        return None
    if not identity_found(row):
        return "B_never_identified"
    reasons = _reasons(row)
    if lead_of(row) == "UNKNOWN" and "no_usable_contact_path" in reasons:
        return "D_identified_contact_missing"
    if lead_of(row) == "UNKNOWN":
        return "A_found_insufficient_evidence"
    return None


def unknown_missing_evidence(row: BusinessCandidate) -> list[str]:
    reasons = _reasons(row)
    missing: list[str] = []
    if not identity_found(row) or "identity_insufficient" in reasons:
        missing.append("identity")
    if "fashion_retail_evidence_missing" in reasons:
        missing.append("fashion/retail evidence")
    if "no_usable_contact_path" in reasons:
        missing.append("contact path")
    if not missing:
        if reasons:
            missing.append(", ".join(reasons))
        else:
            missing.append("other")
    return missing


def lead_card(row: BusinessCandidate) -> dict[str, Any]:
    evidence = row.evidence or {}
    validated = (evidence.get("ai") or {}).get("validated") or {}
    paths = contact_paths(row)
    return {
        "business_name": row.business_name,
        "city": row.city,
        "business_type": row.business_type.value,
        "website": row.website,
        "instagram": row.instagram,
        "facebook": row.facebook,
        "email": row.email,
        "phone": row.phone,
        "address": row.address,
        "potential_stockist": _stockist(row),
        "stockist_lead": lead_of(row),
        "confidence": row.confidence.value,
        "evidence": list(validated.get("evidence") or []),
        "stockist_lead_reasons": _reasons(row),
        "identity_verified_from": identity_verified_from(row),
        "source_type": row.source_type,
        "source_url": row.source_url,
        "manual_review": bool(evidence.get("manual_review")),
        "google_maps_url": paths.get("google_maps_url"),
        "entity_is_business": evidence.get("entity_is_business"),
        "entity_relationship": evidence.get("entity_relationship"),
        "geographic_relevance": evidence.get("geographic_relevance"),
        "geographic_evidence": list(evidence.get("geographic_evidence") or []),
        "business_context": evidence.get("business_context"),
        "entity_quality": evidence.get("entity_quality"),
        "missing_evidence": unknown_missing_evidence(row) if lead_of(row) == "UNKNOWN" else [],
        "failure_class": failure_class(row),
    }


def _contact_count(row: BusinessCandidate) -> int:
    return sum(1 for key in CONTACT_KEYS if _has(getattr(row, key)))


def _only_social(row: BusinessCandidate) -> bool:
    return not _has(row.website) and (_has(row.instagram) or _has(row.facebook))


def _official_website(row: BusinessCandidate) -> bool:
    return _has(row.website) and row.source_type == ResultType.WEBSITE.value


def empty_pair_snapshot(snapshot: dict | None) -> bool:
    if not snapshot:
        return True
    return snapshot.get("business_name") is None and snapshot.get("source_url") is None


def build_organic_report(
    *,
    raw_search_count: int,
    organic_candidates: int,
    rows: list[BusinessCandidate],
    pair_records: list[dict] | None = None,
) -> dict[str, Any]:
    pair_records = pair_records or []
    not_evaluated = sum(
        1 for item in pair_records if empty_pair_snapshot(item.get("rules"))
    )
    evaluated = max(0, organic_candidates - not_evaluated) if pair_records else len(rows)
    yes_leads = [row for row in rows if lead_of(row) == "YES"]
    unknown_leads = [row for row in rows if lead_of(row) == "UNKNOWN"]
    no_leads = [row for row in rows if lead_of(row) == "NO"]
    classes = {
        "A_found_insufficient_evidence": 0,
        "B_never_identified": 0,
        "C_listing_mistaken_for_business": 0,
        "D_identified_contact_missing": 0,
    }
    for row in rows:
        kind = failure_class(row)
        if kind:
            classes[kind] += 1
    stockist = {"YES": 0, "NO": 0, "UNKNOWN": 0}
    for row in rows:
        stockist[_stockist(row)] += 1
    return {
        "organic_candidates": organic_candidates,
        "unique_candidates_after_dedup": len(rows),
        "raw_search_results": raw_search_count,
        "valid_business_entities": sum(1 for row in rows if is_valid_business_entity(row)),
        "invalid_article_directory_listing": sum(1 for row in rows if is_listing_page(row)),
        "verified_identities": sum(1 for row in rows if has_verified_identity(row)),
        "identity_failures": sum(1 for row in rows if not has_verified_identity(row)),
        "identity_found": sum(1 for row in rows if identity_found(row)),
        "identity_missing": sum(1 for row in rows if not _has(row.business_name)),
        "evaluated": evaluated,
        "not_evaluated": not_evaluated,
        "fashion_retail_relevant": sum(1 for row in rows if fashion_retail_relevant(row)),
        "potential_stockist": stockist,
        "stockist_lead": {
            "YES": len(yes_leads),
            "NO": len(no_leads),
            "UNKNOWN": len(unknown_leads),
        },
        "leads_with_website": sum(1 for row in yes_leads if _has(row.website)),
        "leads_with_instagram": sum(1 for row in yes_leads if _has(row.instagram)),
        "leads_with_facebook": sum(1 for row in yes_leads if _has(row.facebook)),
        "leads_with_email": sum(1 for row in yes_leads if _has(row.email)),
        "leads_with_phone": sum(1 for row in yes_leads if _has(row.phone)),
        "leads_with_address": sum(1 for row in yes_leads if _has(row.address)),
        "leads_with_multiple_contacts": sum(
            1 for row in yes_leads if _contact_count(row) >= 2
        ),
        "leads_without_address": sum(1 for row in yes_leads if not _has(row.address)),
        "leads_with_only_social_identity": sum(1 for row in yes_leads if _only_social(row)),
        "leads_with_verified_official_website": sum(
            1 for row in yes_leads if _official_website(row)
        ),
        "manual_review_true": sum(
            1 for row in rows if (row.evidence or {}).get("manual_review")
        ),
        "article_media_candidates": sum(
            1
            for row in rows
            if _entity_field(row, "entity_relationship") in {"ARTICLE", "MEDIA"}
            or row.source_type == ResultType.ARTICLE.value
        ),
        "social_post_candidates": sum(
            1
            for row in rows
            if _entity_field(row, "entity_relationship") == "SOCIAL_POST"
            or "social_post" in _signals(row)
        ),
        "directory_candidates": sum(
            1
            for row in rows
            if _entity_field(row, "entity_relationship") == "DIRECTORY"
            or row.source_type == ResultType.DIRECTORY.value
        ),
        "valid_self_business_entities": sum(
            1
            for row in rows
            if _entity_field(row, "entity_relationship") == "SELF"
            and _entity_field(row, "entity_is_business") == "YES"
        ),
        "mentioned_businesses_extracted": sum(
            1 for row in rows if _entity_field(row, "entity_relationship") == "MENTIONED_BUSINESS"
        ),
        "geographic_relevance": {
            "YES": sum(1 for row in rows if _entity_field(row, "geographic_relevance") == "YES"),
            "NO": sum(1 for row in rows if _entity_field(row, "geographic_relevance") == "NO"),
            "UNKNOWN": sum(
                1
                for row in rows
                if _entity_field(row, "geographic_relevance") in {None, "UNKNOWN"}
            ),
        },
        "wrong_city_or_country": sum(
            1 for row in rows if _entity_field(row, "geographic_relevance") == "NO"
        ),
        "hotel_resort_boutiques": sum(
            1
            for row in rows
            if _entity_field(row, "business_context") == "HOTEL_RESORT_BOUTIQUE"
        ),
        "excluded_before_stockist_eval": sum(
            1 for row in rows if (row.evidence or {}).get("excluded_from_lead_eval")
        ),
        "stockist_leads_after_entity_filter": len(yes_leads),
        "valid_goa_businesses": sum(
            1
            for row in rows
            if _entity_field(row, "entity_relationship") == "SELF"
            and _entity_field(row, "entity_is_business") == "YES"
            and _entity_field(row, "geographic_relevance") == "YES"
        ),
        "failure_classes": classes,
        "yes_leads": [lead_card(row) for row in yes_leads],
        "unknown_leads_sample": [lead_card(row) for row in unknown_leads[:10]],
    }


def latest_seed_benchmark(output_dir: Path) -> Path | None:
    reports = sorted(output_dir.glob("benchmark_goa_*.json"))
    return reports[-1] if reports else None


def compare_seed_benchmark(
    organic: dict[str, Any],
    seed_report: dict[str, Any],
    seed_names: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    seeds = list(seed_report.get("seeds") or [])
    seed_leads_yes = [row for row in seeds if row.get("stockist_lead") == "YES"]
    organic_yes = list(organic.get("yes_leads") or [])

    def _haystack(item: dict) -> str:
        return " ".join(
            str(part).lower()
            for part in (
                item.get("business_name") or item.get("seed_name"),
                item.get("website"),
                item.get("instagram"),
                item.get("source_url"),
            )
            if part
        )

    organic_hay = " ".join(_haystack(item) for item in organic_yes)
    seeds_in_organic_leads = [
        name for name in seed_names if name.lower() in organic_hay
    ]
    organic_not_in_seeds = [
        item["business_name"]
        for item in organic_yes
        if not any(name.lower() in _haystack(item) for name in seed_names)
    ]
    return {
        "seed_stockist_yes": sum(1 for row in seeds if row.get("potential_stockist") == "YES"),
        "seed_stockist_no": sum(1 for row in seeds if row.get("potential_stockist") == "NO"),
        "seed_stockist_unknown": sum(
            1 for row in seeds if row.get("potential_stockist") == "UNKNOWN"
        ),
        "seed_leads_yes": len(seed_leads_yes),
        "seed_lead_names": [row.get("seed_name") for row in seed_leads_yes],
        "organic_leads_yes": len(organic_yes),
        "organic_lead_names": [item.get("business_name") for item in organic_yes],
        "seed_names_found_as_organic_leads": seeds_in_organic_leads,
        "organic_leads_not_in_seed_list": organic_not_in_seeds,
        "seeds_not_found_as_organic_leads": [
            name for name in seed_names if name not in seeds_in_organic_leads
        ],
    }


def load_seed_comparison(
    organic: dict[str, Any],
    output_dir: Path,
    seed_names: tuple[str, ...] | list[str],
    seed_path: Path | None = None,
) -> dict[str, Any] | None:
    path = seed_path or latest_seed_benchmark(output_dir)
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    comparison = compare_seed_benchmark(organic, payload, seed_names)
    comparison["seed_report_path"] = str(path)
    return comparison


def print_organic_report(block: dict[str, Any]) -> None:
    print("=== ORGANIC DISCOVERY FUNNEL ===")
    print(f"organic candidates: {block['organic_candidates']}")
    print(f"unique candidates after dedup: {block['unique_candidates_after_dedup']}")
    print(f"raw search results: {block['raw_search_results']}")
    print(
        f"raw organic candidates → valid Goa businesses → stockist leads: "
        f"{block['organic_candidates']} → {block.get('valid_goa_businesses', 0)} → "
        f"{block.get('stockist_leads_after_entity_filter', block['stockist_lead']['YES'])}"
    )
    print(f"valid business entities: {block['valid_business_entities']}")
    print(f"valid SELF business entities: {block.get('valid_self_business_entities', 0)}")
    print(f"invalid article/directory/listing: {block['invalid_article_directory_listing']}")
    print(f"article/media candidates: {block.get('article_media_candidates', 0)}")
    print(f"social-post candidates: {block.get('social_post_candidates', 0)}")
    print(f"directory candidates: {block.get('directory_candidates', 0)}")
    print(f"mentioned businesses extracted: {block.get('mentioned_businesses_extracted', 0)}")
    geo = block.get("geographic_relevance") or {}
    print(
        f"geographic relevance YES/NO/UNKNOWN: "
        f"{geo.get('YES', 0)} / {geo.get('NO', 0)} / {geo.get('UNKNOWN', 0)}"
    )
    print(f"wrong-city/wrong-country: {block.get('wrong_city_or_country', 0)}")
    print(f"hotel/resort boutiques: {block.get('hotel_resort_boutiques', 0)}")
    print(f"excluded before stockist evaluation: {block.get('excluded_before_stockist_eval', 0)}")
    print(f"stockist leads after entity-quality filter: {block.get('stockist_leads_after_entity_filter', 0)}")
    print(f"verified identities: {block['verified_identities']}")
    print(f"identity failures: {block['identity_failures']}")
    print(f"identity_found: {block['identity_found']}")
    print(f"identity_missing: {block['identity_missing']}")
    print(f"evaluated: {block['evaluated']}")
    print(f"not_evaluated: {block['not_evaluated']}")
    print(f"fashion/retail relevant: {block['fashion_retail_relevant']}")
    stockist = block["potential_stockist"]
    print(
        f"potential_stockist YES/NO/UNKNOWN: "
        f"{stockist['YES']} / {stockist['NO']} / {stockist['UNKNOWN']}"
    )
    leads = block["stockist_lead"]
    print(
        f"stockist_lead YES/NO/UNKNOWN: "
        f"{leads['YES']} / {leads['NO']} / {leads['UNKNOWN']}"
    )
    print(f"leads with website: {block['leads_with_website']}")
    print(f"leads with Instagram: {block['leads_with_instagram']}")
    print(f"leads with Facebook: {block['leads_with_facebook']}")
    print(f"leads with email: {block['leads_with_email']}")
    print(f"leads with phone: {block['leads_with_phone']}")
    print(f"leads with address: {block['leads_with_address']}")
    print(f"leads with multiple contacts: {block['leads_with_multiple_contacts']}")
    print(f"leads without address: {block['leads_without_address']}")
    print(f"leads with only social identity: {block['leads_with_only_social_identity']}")
    print(f"leads with verified official website: {block['leads_with_verified_official_website']}")
    print(f"manual_review = true: {block['manual_review_true']}")
    print()
    print("FAILURE CLASSES (not the same thing)")
    classes = block["failure_classes"]
    print(f"A found but insufficient evidence: {classes['A_found_insufficient_evidence']}")
    print(f"B never correctly identified: {classes['B_never_identified']}")
    print(f"C listing mistaken for a business: {classes['C_listing_mistaken_for_business']}")
    print(f"D identified but contact missing: {classes['D_identified_contact_missing']}")
    print()
    print("=== STOCKIST LEAD YES (organic) ===")
    if not block["yes_leads"]:
        print("(none)")
    for item in block["yes_leads"]:
        print(f"- {item['business_name']} | {item.get('website') or item.get('source_url')}")
        print(
            f"  city={item['city']} type={item['business_type']} "
            f"stockist={item['potential_stockist']} lead={item['stockist_lead']} "
            f"confidence={item['confidence']}"
        )
        print(
            f"  website={item['website'] or '—'} instagram={item['instagram'] or '—'} "
            f"facebook={item['facebook'] or '—'}"
        )
        print(
            f"  email={item['email'] or '—'} phone={item['phone'] or '—'} "
            f"address={item['address'] or '—'}"
        )
        print(f"  identity_verified_from={item['identity_verified_from'] or '—'}")
        print(
            f"  entity={item.get('entity_relationship')} "
            f"business={item.get('entity_is_business')} "
            f"geo={item.get('geographic_relevance')} "
            f"context={item.get('business_context')} "
            f"quality={item.get('entity_quality')}"
        )
        print(f"  reasons={item['stockist_lead_reasons']}")
        if item["evidence"]:
            print(f"  evidence={item['evidence']}")
    print()
    print("=== STOCKIST LEAD UNKNOWN (top 10) ===")
    if not block["unknown_leads_sample"]:
        print("(none)")
    for item in block["unknown_leads_sample"]:
        print(f"- {item['business_name']} | {item.get('website') or item.get('source_url')}")
        print(f"  missing: {item['missing_evidence']} class={item['failure_class'] or '—'}")
    print()


def print_seed_comparison(block: dict[str, Any]) -> None:
    print("=== SEED COMPARISON (organic run did not inject seeds) ===")
    print(f"previous seed report: {block.get('seed_report_path')}")
    print(
        f"seed potential_stockist YES/NO/UNKNOWN: "
        f"{block['seed_stockist_yes']} / {block['seed_stockist_no']} / "
        f"{block['seed_stockist_unknown']}"
    )
    print(f"seed stockist_lead YES: {block['seed_leads_yes']} {block['seed_lead_names']}")
    print(
        f"organic stockist_lead YES: {block['organic_leads_yes']} "
        f"{block['organic_lead_names']}"
    )
    print(f"seed names found as organic leads: {block['seed_names_found_as_organic_leads']}")
    print(f"organic leads not in the 8-seed list: {block['organic_leads_not_in_seed_list']}")
    print(f"seeds not found as organic leads: {block['seeds_not_found_as_organic_leads']}")
    print()
