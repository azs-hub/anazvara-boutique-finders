"""Offline stockist-lead export and consistency gate.

Reads an existing benchmark JSON. Does not search, fetch, enrich, or call Qwen.
Does not rewrite production classification fields on the source file.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from business_candidates import (
    UNKNOWN,
    BusinessCandidate,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
    _merge_pair,
)
from classification import ResultType, classify_url
from deduplication import (
    normalize_instagram,
    normalize_name_city,
    normalize_phone,
    normalize_website_domain,
)
from entity_quality import is_social_post_url, looks_like_media_or_listicle
from organic_lead_report import is_listing_page
from stockist_lead import (
    LEAD_NO_RE,
    LEAD_RELEVANCE_RE,
    contact_paths,
    _contactable,
    _fashion_or_retail,
    _own_label_only,
)

GROUP_LEAD = "LEAD"
GROUP_REVIEW = "REVIEW"
GROUP_EXCLUDED = "EXCLUDED"
GROUP_ORDER = {GROUP_LEAD: 0, GROUP_REVIEW: 1, GROUP_EXCLUDED: 2}

YES_NO_UNKNOWN = {"YES", "NO", "UNKNOWN"}
NON_BUSINESS_RELATIONSHIPS = {"ARTICLE", "MEDIA", "DIRECTORY", "SOCIAL_POST"}
ARTICLE_REJECTIONS = {
    "directory_or_article_not_business",
    "roundup_or_publisher",
    "discovery_page_not_stockist",
    "not_a_business",
}
OWN_LABEL_REJECTIONS = {
    "own_label_not_stockist",
}
SKIPPED_NOT_REJECTION = {
    "source_not_website",
    "source_not_official_site",
    "rules_confident",
    "disabled",
    "identity_not_confident",
}

QWEN_LISTICLE_RE = re.compile(
    r"\b(listicle|directory|shopping guide|curated list|"
    r"not the business itself|media outlet|news article|"
    r"press release|publisher|roundup)\b",
    re.IGNORECASE,
)
QWEN_OWN_LABEL_RE = re.compile(
    r"\b(own[\s\-]?label|own[\s\-]?brand|single[\s\-]?brand|"
    r"mono[\s\-]?brand|only (?:its|their|our) own|"
    r"does not stock (?:external|other)|proprietary to the brand|"
    r"selling only (?:its|their) own)\b",
    re.IGNORECASE,
)
QWEN_UNRELATED_RE = re.compile(
    r"\b(marketplace|ticketing|event platform|news (?:outlet|site|article)|"
    r"media (?:outlet|company)|not a (?:fashion )?retailer|"
    r"not a physical boutique|wholesale only|fashion school)\b",
    re.IGNORECASE,
)
LISTING_PATH_RE = re.compile(
    r"/(place|places|listings?|store-locator|stores|brands?)/|"
    r"boutiques?-to-(?:visit|shop)",
    re.IGNORECASE,
)
STRONG_STOCKIST_RE = re.compile(
    r"\b(concept\s+store|multi[\s\-]?brand|multi[\s\-]?designer|"
    r"independent\s+(?:designers?|labels?|brands?)|"
    r"curated\s+(?:lifestyle\s+)?(?:fashion\s+)?(?:store|collections?|selection)|"
    r"carrying other brands|carries (?:other|multiple) (?:brands|labels)|"
    r"multiple independent)\b",
    re.IGNORECASE,
)
WEAK_QWEN_RE = re.compile(
    r"text excerpt is empty|evidence required.{0,80}missing|"
    r"no explicit mention of carrying other brands|"
    r"coming soon|cannot confirm|structured facts are missing",
    re.IGNORECASE,
)

CSV_COLUMNS = [
    "business_name",
    "city",
    "address",
    "website",
    "instagram",
    "facebook",
    "phone",
    "email",
    "export_group",
    "export_reason",
    "business_type",
    "women_fashion_relevance",
    "physical_store",
    "carries_other_brands",
    "potential_stockist",
    "stockist_lead",
    "confidence",
    "entity_quality",
    "manual_review",
    "discovered_by_queries",
    "source_urls",
    "export_evidence",
    "state",
    "country",
    "physical_store_confidence",
    "fashion_relevance",
    "retail_model",
    "multi_brand_confidence",
    "anazvara_fit_score",
    "geography_confidence",
    "business_confidence",
    "discovery_source",
    "discovery_source_url",
    "discovery_source_type",
    "evidence_physical_store",
    "evidence_multi_brand",
    "evidence_fashion",
    "evidence_fit",
    "reason",
]

OUTPUT_FILES = {
    "all_csv": "stockist_leads.csv",
    "all_json": "stockist_leads.json",
    "summary": "stockist_export_summary.json",
    "leads_csv": "stockist_leads_only.csv",
    "review_csv": "stockist_review_only.csv",
    "excluded_csv": "stockist_excluded.csv",
}


def _has(value: Any) -> bool:
    return bool(value) and value != UNKNOWN


def _upper(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    return text if text else default


def _enum_or(enum_cls, value: Any, fallback):
    text = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    try:
        return enum_cls(text)
    except ValueError:
        return fallback


def _present(value: Any) -> str:
    return "YES" if _has(value) else "NO"


def _join(values: list[Any]) -> str:
    return "; ".join(str(item) for item in values if item not in {None, ""})


def _evidence(row: BusinessCandidate) -> dict[str, Any]:
    return dict(row.evidence or {})


def _ai_block(row: BusinessCandidate) -> dict[str, Any]:
    return dict((_evidence(row).get("ai") or {}))


def _validated(row: BusinessCandidate) -> dict[str, Any]:
    return dict(_ai_block(row).get("validated") or {})


def _qwen_attempted(row: BusinessCandidate) -> bool:
    return bool(_ai_block(row).get("attempted"))


def _qwen_skipped_reason(row: BusinessCandidate) -> str | None:
    reason = _ai_block(row).get("skipped_reason")
    if reason in {None, ""}:
        return None
    return str(reason)


def _qwen_skipped_not_rejection(row: BusinessCandidate) -> bool:
    if _qwen_attempted(row):
        return False
    reason = _qwen_skipped_reason(row)
    return reason in SKIPPED_NOT_REJECTION or reason is not None


def _stockist_lead(row: BusinessCandidate) -> str:
    value = _upper(_evidence(row).get("stockist_lead"))
    return value if value in YES_NO_UNKNOWN else "UNKNOWN"


def _potential_stockist(row: BusinessCandidate) -> str:
    value = _upper(_evidence(row).get("ai_potential_stockist"))
    if value in YES_NO_UNKNOWN:
        return value
    validated = _upper(_validated(row).get("potential_stockist"))
    return validated if validated in YES_NO_UNKNOWN else "UNKNOWN"


def _carries_other_brands(row: BusinessCandidate) -> str:
    value = _upper(_validated(row).get("carries_other_brands"))
    return value if value in YES_NO_UNKNOWN else "UNKNOWN"


def _entity_field(row: BusinessCandidate, key: str, default: str | None = None) -> str | None:
    value = _evidence(row).get(key)
    if value in {None, ""}:
        return default
    return str(value)


def _relationship(row: BusinessCandidate) -> str:
    return _upper(_entity_field(row, "entity_relationship"), "UNKNOWN")


def _is_business_flag(row: BusinessCandidate) -> str:
    return _upper(_entity_field(row, "entity_is_business"), "UNKNOWN")


def _geo(row: BusinessCandidate) -> str:
    return _upper(_entity_field(row, "geographic_relevance"), "UNKNOWN")


def _qwen_is_business(row: BusinessCandidate) -> bool | None:
    value = _validated(row).get("is_business")
    if isinstance(value, bool):
        return value
    raw = _evidence(row).get("ai_is_business")
    if isinstance(raw, bool):
        return raw
    return None


def _rejected(row: BusinessCandidate) -> list[str]:
    return [str(item) for item in (_validated(row).get("rejected") or [])]


def _qwen_evidence(row: BusinessCandidate) -> list[str]:
    return [str(item) for item in (_validated(row).get("evidence") or [])]


def _lead_reasons(row: BusinessCandidate) -> list[str]:
    return [str(item) for item in (_evidence(row).get("stockist_lead_reasons") or [])]


def _signals(row: BusinessCandidate) -> list[str]:
    return [str(item) for item in (_evidence(row).get("signals") or [])]


def _page_title(row: BusinessCandidate) -> str:
    payload = ((_evidence(row).get("qwen_inspection") or {}).get("payload") or {})
    return str(payload.get("page_title") or "")


def _text_excerpt(row: BusinessCandidate) -> str:
    payload = ((_evidence(row).get("qwen_inspection") or {}).get("payload") or {})
    return str(payload.get("text_excerpt") or "")


def _record_text(row: BusinessCandidate) -> str:
    return " ".join(
        part
        for part in (
            row.business_name or "",
            _page_title(row),
            _text_excerpt(row)[:2000],
            " ".join(_qwen_evidence(row)),
        )
        if part
    )


def discovered_queries(row: BusinessCandidate) -> list[str]:
    evidence = _evidence(row)
    queries = list(evidence.get("discovered_by_queries") or [])
    single = evidence.get("discovery_query")
    if single:
        queries.append(str(single))
    return list(dict.fromkeys(str(item) for item in queries if item))


def source_urls_of(row: BusinessCandidate) -> list[str]:
    evidence = _evidence(row)
    urls = [row.source_url, evidence.get("source_url")]
    urls.extend(evidence.get("merged_source_urls") or [])
    return list(dict.fromkeys(str(item) for item in urls if item))


def business_from_dict(payload: dict[str, Any]) -> BusinessCandidate:
    """Rebuild a BusinessCandidate from a serialized benchmark row."""
    return BusinessCandidate(
        business_name=payload.get("business_name") or UNKNOWN,
        website=payload.get("website"),
        instagram=payload.get("instagram"),
        facebook=payload.get("facebook"),
        whatsapp=payload.get("whatsapp"),
        phone=payload.get("phone"),
        email=payload.get("email"),
        address=payload.get("address"),
        city=payload.get("city") or UNKNOWN,
        source_url=payload.get("source_url") or "",
        source_type=str(payload.get("source_type") or ResultType.UNKNOWN.value),
        business_type=_enum_or(BusinessType, payload.get("business_type"), BusinessType.UNKNOWN),
        fashion_relevance=_enum_or(Relevance, payload.get("fashion_relevance"), Relevance.UNKNOWN),
        women_fashion_relevance=_enum_or(
            Relevance, payload.get("women_fashion_relevance"), Relevance.UNKNOWN
        ),
        physical_store=_enum_or(
            PhysicalStore, payload.get("physical_store"), PhysicalStore.UNKNOWN
        ),
        evidence=dict(payload.get("evidence") or {}),
        confidence=_enum_or(Confidence, payload.get("confidence"), Confidence.LOW),
        extra_phones=list(payload.get("extra_phones") or []),
        extra_emails=list(payload.get("extra_emails") or []),
    )


def extract_business_records(payload: Any) -> list[dict[str, Any]]:
    """Return final processed business rows. Search hits are ignored."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise ValueError("Benchmark file must be a JSON object or array")
    rows = payload.get("business_candidates_llm")
    if not isinstance(rows, list):
        raise ValueError("Benchmark is missing business_candidates_llm")
    return [item for item in rows if isinstance(item, dict)]


def load_benchmark(path: Path | str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, (dict, list)):
        raise ValueError("Benchmark file must be a JSON object or array")
    return {"payload": payload, "records": extract_business_records(payload)}


def _official_website_domain(row: BusinessCandidate) -> str | None:
    domain = normalize_website_domain(row.website)
    if not domain:
        return None
    if classify_url(row.website or "") in {
        ResultType.ARTICLE,
        ResultType.DIRECTORY,
        ResultType.VIDEO,
    }:
        return None
    if looks_like_media_or_listicle(
        url=row.website,
        title=_page_title(row) or row.business_name,
        text="",
        source_type=row.source_type,
        signals=_signals(row),
    ):
        return None
    source_domain = normalize_website_domain(row.source_url)
    if row.source_type in {ResultType.ARTICLE.value, ResultType.DIRECTORY.value}:
        if source_domain and domain == source_domain:
            return None
    return domain


def _official_instagram(row: BusinessCandidate) -> str | None:
    if is_social_post_url(row.instagram) or is_social_post_url(row.source_url):
        return None
    return normalize_instagram(row.instagram)


def _phone_key(row: BusinessCandidate) -> str | None:
    digits = normalize_phone(row.phone)
    if not digits or len(digits) < 10:
        return None
    return digits


def _name_city_key(row: BusinessCandidate) -> str | None:
    return normalize_name_city(row.business_name, row.city)


def _conflicting_strong_identity(left: BusinessCandidate, right: BusinessCandidate) -> bool:
    left_site = _official_website_domain(left)
    right_site = _official_website_domain(right)
    if left_site and right_site and left_site != right_site:
        return True
    left_ig = _official_instagram(left)
    right_ig = _official_instagram(right)
    return bool(left_ig and right_ig and left_ig != right_ig)


def _merge_pair_export(left: BusinessCandidate, right: BusinessCandidate) -> BusinessCandidate:
    merged = _merge_pair(left, right)
    evidence = dict(merged.evidence or {})
    evidence["discovered_by_queries"] = list(
        dict.fromkeys(discovered_queries(left) + discovered_queries(right))
    )
    if evidence["discovered_by_queries"]:
        evidence["discovery_query"] = evidence["discovered_by_queries"][0]
    evidence["merged_source_urls"] = list(
        dict.fromkeys(source_urls_of(left) + source_urls_of(right))
    )
    return BusinessCandidate(
        business_name=merged.business_name,
        website=merged.website,
        instagram=merged.instagram,
        facebook=merged.facebook,
        whatsapp=merged.whatsapp,
        phone=merged.phone,
        email=merged.email,
        address=merged.address,
        city=merged.city,
        source_url=merged.source_url,
        source_type=merged.source_type,
        business_type=merged.business_type,
        fashion_relevance=merged.fashion_relevance,
        women_fashion_relevance=merged.women_fashion_relevance,
        physical_store=merged.physical_store,
        evidence=evidence,
        confidence=merged.confidence,
        extra_phones=merged.extra_phones,
        extra_emails=merged.extra_emails,
    )


def merge_business_records(
    rows: list[BusinessCandidate],
) -> tuple[list[BusinessCandidate], int, list[dict[str, Any]]]:
    """Merge only on verified identity. Ambiguous name matches stay separate."""
    if not rows:
        return [], 0, []
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        parent[find(left)] = find(right)

    indexes: dict[tuple[str, str], int] = {}
    ambiguous: list[dict[str, Any]] = []

    def claim(kind: str, key: str, index: int, *, allow_conflict_check: bool = False) -> None:
        token = (kind, key)
        if token not in indexes:
            indexes[token] = index
            return
        other = indexes[token]
        if allow_conflict_check and _conflicting_strong_identity(rows[index], rows[other]):
            ambiguous.append(
                {
                    "reason": "ambiguous_identity_not_merged",
                    "key": f"{kind}:{key}",
                    "left": rows[index].business_name,
                    "right": rows[other].business_name,
                }
            )
            return
        union(index, other)

    for index, row in enumerate(rows):
        website = _official_website_domain(row)
        if website:
            claim("website", website, index)
        instagram = _official_instagram(row)
        if instagram:
            claim("instagram", instagram, index)
        phone = _phone_key(row)
        if phone:
            claim("phone", phone, index)
        name_city = _name_city_key(row)
        if name_city:
            claim("name_city", name_city, index, allow_conflict_check=True)

    groups: dict[int, list[int]] = {}
    for index in range(len(rows)):
        groups.setdefault(find(index), []).append(index)
    merged_rows: list[BusinessCandidate] = []
    for members in groups.values():
        combined = rows[members[0]]
        for extra in members[1:]:
            combined = _merge_pair_export(combined, rows[extra])
        merged_rows.append(combined)
    duplicates_merged = len(rows) - len(merged_rows)
    return merged_rows, duplicates_merged, ambiguous


def _looks_like_non_business_page(row: BusinessCandidate) -> bool:
    relationship = _relationship(row)
    if relationship in NON_BUSINESS_RELATIONSHIPS:
        return True
    if relationship != "MENTIONED_BUSINESS" and is_listing_page(row):
        return True
    if is_social_post_url(row.source_url) or is_social_post_url(row.instagram):
        return True
    if any(item in ARTICLE_REJECTIONS for item in _rejected(row)):
        return True
    title = _page_title(row) or row.business_name
    inspect_type = "WEBSITE" if relationship == "MENTIONED_BUSINESS" else row.source_type
    if looks_like_media_or_listicle(
        url=row.website or row.source_url,
        title=title,
        text=_record_text(row),
        source_type=inspect_type,
        signals=_signals(row),
    ):
        return True
    if QWEN_LISTICLE_RE.search(" ".join(_qwen_evidence(row))):
        return True
    if LISTING_PATH_RE.search(row.website or "") and QWEN_LISTICLE_RE.search(_record_text(row)):
        return True
    return False


def _own_label_with_evidence(row: BusinessCandidate) -> bool:
    if _carries_other_brands(row) == "YES":
        return False
    items = _qwen_evidence(row)
    if any(
        STRONG_STOCKIST_RE.search(item) and not _uncertain_evidence_item(item)
        for item in items
    ):
        return False
    rejected = _rejected(row)
    if any(item in OWN_LABEL_REJECTIONS for item in rejected):
        return True
    positioning = _upper(_validated(row).get("designer_positioning"))
    if positioning == "OWN_LABEL" and _carries_other_brands(row) != "YES":
        return True
    blob = " ".join(items)
    if _potential_stockist(row) == "NO" and QWEN_OWN_LABEL_RE.search(blob):
        return True
    return _own_label_only(row, _record_text(row))


def _unrelated_with_evidence(row: BusinessCandidate) -> bool:
    context = _upper(_entity_field(row, "business_context"), "UNKNOWN")
    if row.business_type is BusinessType.MARKETPLACE or context == "MARKETPLACE":
        return True
    if context == "HOTEL_RESORT_BOUTIQUE" and not _fashion_retail_evidence(row):
        return True
    blob = " ".join(_qwen_evidence(row) + [_record_text(row)])
    if QWEN_UNRELATED_RE.search(" ".join(_qwen_evidence(row))):
        return True
    return bool(LEAD_NO_RE.search(blob))


def _identity_invalid(row: BusinessCandidate) -> bool:
    if _is_business_flag(row) == "NO" and _relationship(row) in NON_BUSINESS_RELATIONSHIPS:
        return True
    if _qwen_is_business(row) is False and _qwen_attempted(row):
        return True
    named = _has(row.business_name)
    if named:
        return False
    paths = contact_paths(row)
    return not any(_has(paths.get(key)) for key in ("website", "instagram", "facebook"))


def _usable_contact(row: BusinessCandidate) -> bool:
    return _contactable(contact_paths(row))


def _fashion_retail_evidence(row: BusinessCandidate) -> bool:
    if "fashion_retail_page_evidence" in _lead_reasons(row):
        return True
    if _potential_stockist(row) == "YES":
        return True
    return _fashion_or_retail(row, _record_text(row))


def _social_retail_evidence(row: BusinessCandidate) -> bool:
    """Social identity needs page/Qwen retail evidence, not a title or type label."""
    if _potential_stockist(row) == "YES" and _qwen_evidence(row):
        return True
    text = " ".join(
        part for part in (*_qwen_evidence(row), _text_excerpt(row)) if part
    )
    if not text.strip():
        return False
    return bool(STRONG_STOCKIST_RE.search(text) or LEAD_RELEVANCE_RE.search(text))


def _uncertain_evidence_item(text: str) -> bool:
    return bool(
        WEAK_QWEN_RE.search(text)
        or re.search(
            r"\b(no explicit mention|cannot confirm|not found|"
            r"to confirm if|missing from|search query suggests|no evidence)\b",
            text,
            re.IGNORECASE,
        )
    )


def _affirmative_stockist_phrase(row: BusinessCandidate) -> bool:
    items = list(_qwen_evidence(row))
    excerpt = _text_excerpt(row)
    if excerpt:
        items.append(excerpt)
    for item in items:
        if _uncertain_evidence_item(item):
            continue
        if STRONG_STOCKIST_RE.search(item):
            return True
    return False


def _strong_stockist_evidence(row: BusinessCandidate) -> bool:
    if _carries_other_brands(row) == "YES":
        return True
    if _affirmative_stockist_phrase(row):
        return True
    qwen_items = _qwen_evidence(row)
    if (
        _potential_stockist(row) == "YES"
        and qwen_items
        and not any(_uncertain_evidence_item(item) for item in qwen_items)
    ):
        return True
    return False


def _identity_weak(row: BusinessCandidate) -> bool:
    quality = _upper(_entity_field(row, "entity_quality"), "UNKNOWN")
    if quality in {"WEAK", "LOW", "REJECTED"}:
        return True
    return "official_identity_weak" in _lead_reasons(row)


def _social_only(row: BusinessCandidate) -> bool:
    return not _has(row.website) and (_has(row.instagram) or _has(row.facebook))


def _third_party_listing(row: BusinessCandidate) -> bool:
    url = row.website or row.source_url or ""
    return bool(LISTING_PATH_RE.search(url))


def detect_contradictions(row: BusinessCandidate) -> list[dict[str, Any]]:
    """Return contradictory field pairs. Missing/skipped Qwen is not a contradiction."""
    conflicts: list[dict[str, Any]] = []
    lead = _stockist_lead(row)
    relationship = _relationship(row)
    geo = _geo(row)
    qwen_business = _qwen_is_business(row)
    qwen_stockist = _upper(_validated(row).get("potential_stockist"))
    if qwen_stockist not in YES_NO_UNKNOWN:
        qwen_stockist = _potential_stockist(row)

    def add(kind: str, detail: str) -> None:
        conflicts.append({"kind": kind, "detail": detail})

    if lead == "YES" and relationship in NON_BUSINESS_RELATIONSHIPS:
        add("lead_yes_but_not_self", f"entity_relationship={relationship}")
    if lead == "YES" and _is_business_flag(row) == "NO":
        add("lead_yes_but_not_business", "entity_is_business=NO")
    if lead == "YES" and geo == "NO":
        add("lead_yes_but_wrong_geo", "geographic_relevance=NO")
    if lead == "YES" and _looks_like_non_business_page(row):
        add("lead_yes_but_article_or_directory", "page is article/directory/social post")
    if lead == "YES" and _entity_field(row, "entity_quality") == "REJECTED":
        add("lead_yes_but_entity_rejected", "entity_quality=REJECTED")
    if lead == "YES" and _own_label_with_evidence(row):
        add("lead_yes_but_own_label", "own-label evidence present")
    if (
        lead == "YES"
        and qwen_business is False
        and _qwen_attempted(row)
        and not _qwen_skipped_not_rejection(row)
    ):
        add("lead_yes_but_qwen_not_business", "qwen validated is_business=false")
    if (
        lead == "YES"
        and qwen_stockist == "NO"
        and _qwen_attempted(row)
        and not _qwen_skipped_not_rejection(row)
    ):
        add("lead_yes_but_qwen_not_stockist", "qwen validated potential_stockist=NO")
    if relationship == "SELF" and _looks_like_non_business_page(row):
        add("self_but_listicle_evidence", "SELF conflicts with article/directory evidence")
    if _is_business_flag(row) == "YES" and relationship in NON_BUSINESS_RELATIONSHIPS:
        add(
            "business_yes_but_article_relationship",
            f"entity_is_business=YES relationship={relationship}",
        )
    return conflicts


def assign_export_group(
    row: BusinessCandidate,
    *,
    ambiguous: bool = False,
) -> dict[str, Any]:
    """Final offline gate. Does not mutate production stockist_lead."""
    conflicts = detect_contradictions(row)
    reasons: list[str] = []
    resolved = False
    unresolved = False
    relationship = _relationship(row)
    geo = _geo(row)
    lead = _stockist_lead(row)
    stockist = _potential_stockist(row)

    if _looks_like_non_business_page(row) or relationship in NON_BUSINESS_RELATIONSHIPS:
        reasons.append(
            f"article_or_directory_not_business:{relationship or row.source_type}"
        )
        return _decision(
            GROUP_EXCLUDED,
            reasons,
            conflicts,
            resolved=bool(conflicts),
            unresolved=False,
        )
    if _identity_invalid(row):
        reasons.append("identity_demonstrably_invalid")
        return _decision(GROUP_EXCLUDED, reasons, conflicts, resolved=bool(conflicts))
    if geo == "NO":
        reasons.append("wrong_geography")
        return _decision(GROUP_EXCLUDED, reasons, conflicts, resolved=bool(conflicts))
    if _own_label_with_evidence(row):
        reasons.append("own_label_not_external_stockist")
        return _decision(GROUP_EXCLUDED, reasons, conflicts, resolved=bool(conflicts))
    if _unrelated_with_evidence(row):
        reasons.append("unrelated_or_non_retail_business")
        return _decision(GROUP_EXCLUDED, reasons, conflicts, resolved=bool(conflicts))
    if relationship == "MENTIONED_BUSINESS":
        reasons.append(
            "mentioned_business_not_self"
            if _fashion_retail_evidence(row) and _usable_contact(row)
            else "mentioned_business_needs_verification"
        )
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=True)
    if relationship != "SELF":
        reasons.append(f"entity_relationship_not_self:{relationship}")
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=True)

    if ambiguous:
        reasons.append("ambiguous_identity_keep_separate")
        unresolved = True
    if conflicts:
        hard = {
            "lead_yes_but_article_or_directory",
            "lead_yes_but_not_self",
            "lead_yes_but_not_business",
            "lead_yes_but_wrong_geo",
            "lead_yes_but_own_label",
            "self_but_listicle_evidence",
        }
        if any(item["kind"] in hard for item in conflicts):
            reasons.append("contradictory_classification_excluded")
            return _decision(GROUP_EXCLUDED, reasons, conflicts, resolved=True)
        reasons.append("contradictory_classification")
        unresolved = True

    if not _has(row.business_name):
        reasons.append("identity_incomplete")
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=unresolved)
    if geo == "UNKNOWN":
        reasons.append("geography_unconfirmed")
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=unresolved)
    if _identity_weak(row):
        reasons.append("official_identity_weak")
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=unresolved)
    if _third_party_listing(row):
        reasons.append("third_party_listing_identity")
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=unresolved)
    if not _usable_contact(row):
        reasons.append("no_usable_contact_path")
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=unresolved)
    if _social_only(row) and not _social_retail_evidence(row):
        reasons.append("social_identity_insufficient_retail_evidence")
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=unresolved)
    if not _fashion_retail_evidence(row):
        reasons.append("fashion_retail_evidence_missing")
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=unresolved)
    if not _strong_stockist_evidence(row):
        reasons.append("multi_brand_status_unknown")
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=unresolved)
    if stockist == "NO":
        reasons.append("potential_stockist_no_without_hard_exclusion")
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=True)
    if unresolved:
        return _decision(GROUP_REVIEW, reasons, conflicts, unresolved=True)

    if lead == "YES":
        reasons.append("consistent_self_business_with_contact_and_retail_evidence")
    elif stockist == "YES":
        reasons.append("qwen_stockist_yes_with_official_identity")
    else:
        reasons.append("plausible_retail_business_with_usable_contact")
    if not reasons:
        reasons.append("approved_stockist_lead")
    return _decision(GROUP_LEAD, reasons, conflicts, resolved=resolved)


def _decision(
    group: str,
    reasons: list[str],
    conflicts: list[dict[str, Any]],
    *,
    resolved: bool = False,
    unresolved: bool = False,
) -> dict[str, Any]:
    return {
        "export_group": group,
        "export_reason": reasons[0] if reasons else "unspecified",
        "export_reasons": reasons,
        "conflicts": conflicts,
        "manual_review": group in {GROUP_LEAD, GROUP_REVIEW},
        "contradiction_resolved": resolved,
        "contradiction_unresolved": unresolved,
    }


def build_export_row(
    row: BusinessCandidate,
    decision: dict[str, Any],
    *,
    records_merged: int = 1,
) -> dict[str, Any]:
    queries = discovered_queries(row)
    urls = source_urls_of(row)
    evidence_items = _qwen_evidence(row)
    return {
        "business_name": row.business_name if _has(row.business_name) else None,
        "city": row.city if _has(row.city) else None,
        "address": row.address if _has(row.address) else None,
        "website": row.website if _has(row.website) else None,
        "instagram": row.instagram if _has(row.instagram) else None,
        "facebook": row.facebook if _has(row.facebook) else None,
        "phone": row.phone if _has(row.phone) else None,
        "email": row.email if _has(row.email) else None,
        "business_type": row.business_type.value,
        "women_fashion_relevance": row.women_fashion_relevance.value,
        "physical_store": row.physical_store.value,
        "carries_other_brands": _carries_other_brands(row),
        "potential_stockist": _potential_stockist(row),
        "stockist_lead": _stockist_lead(row),
        "export_group": decision["export_group"],
        "export_reason": decision["export_reason"],
        "export_reasons": decision["export_reasons"],
        "confidence": row.confidence.value,
        "entity_quality": _entity_field(row, "entity_quality"),
        "entity_is_business": _is_business_flag(row),
        "entity_relationship": _relationship(row),
        "geographic_relevance": _geo(row),
        "geographic_evidence": list(_evidence(row).get("geographic_evidence") or []),
        "business_context": _entity_field(row, "business_context"),
        "manual_review": decision["manual_review"],
        "discovered_by_queries": queries,
        "source_urls": urls,
        "source_type": row.source_type,
        "source_url": row.source_url or None,
        "qwen_attempted": _qwen_attempted(row),
        "qwen_skipped_reason": _qwen_skipped_reason(row),
        "qwen_is_business": _qwen_is_business(row),
        "qwen_rejected": _rejected(row),
        "qwen_evidence": evidence_items,
        "stockist_lead_reasons": _lead_reasons(row),
        "conflicts": decision["conflicts"],
        "records_merged": records_merged,
        "export_evidence": evidence_items or _lead_reasons(row),
        **_fit_export_fields(row),
    }


def _fit_block(row: BusinessCandidate) -> dict[str, Any]:
    fit = _evidence(row).get("fit")
    return dict(fit) if isinstance(fit, dict) else {}


def _fit_export_fields(row: BusinessCandidate) -> dict[str, Any]:
    fit = _fit_block(row)
    evidence = _evidence(row)
    sources = evidence.get("discovery_sources") or []
    first = sources[0] if sources else {}
    return {
        "state": evidence.get("expected_state") or evidence.get("state") or "",
        "country": evidence.get("expected_country") or evidence.get("country") or "",
        "physical_store_confidence": fit.get("physical_store_confidence") or "",
        "fashion_relevance": fit.get("fashion_relevance") or row.fashion_relevance.value,
        "retail_model": fit.get("retail_model") or evidence.get("retail_model") or "",
        "multi_brand_confidence": fit.get("multi_brand_confidence") or "",
        "anazvara_fit_score": fit.get("anazvara_fit_score", ""),
        "geography_confidence": fit.get("geography_confidence") or "",
        "business_confidence": fit.get("business_confidence") or "",
        "discovery_source": evidence.get("discovered_from") or (first.get("type") if isinstance(first, dict) else ""),
        "discovery_source_url": evidence.get("discovery_source_url") or (first.get("url") if isinstance(first, dict) else ""),
        "discovery_source_type": evidence.get("discovery_source_type") or (first.get("type") if isinstance(first, dict) else ""),
        "evidence_physical_store": fit.get("evidence_physical_store") or [],
        "evidence_multi_brand": fit.get("evidence_multi_brand") or [],
        "evidence_fashion": fit.get("evidence_fashion") or [],
        "evidence_fit": fit.get("evidence_fit") or [],
        "reason": fit.get("reason") or "",
        "status": fit.get("status") or "",
        "explanation": fit.get("explanation") or evidence.get("explanation") or "",
    }


def _csv_view(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("export_evidence") or []
    evidence_text = _join(evidence)
    if len(evidence_text) > 500:
        evidence_text = evidence_text[:497] + "..."
    return {
        "business_name": row.get("business_name") or "",
        "city": row.get("city") or "",
        "address": row.get("address") or "",
        "website": row.get("website") or "",
        "instagram": row.get("instagram") or "",
        "facebook": row.get("facebook") or "",
        "phone": row.get("phone") or "",
        "email": row.get("email") or "",
        "export_group": row.get("export_group") or "",
        "export_reason": row.get("export_reason") or "",
        "business_type": row.get("business_type") or "",
        "women_fashion_relevance": row.get("women_fashion_relevance") or "",
        "physical_store": row.get("physical_store") or "",
        "carries_other_brands": row.get("carries_other_brands") or "",
        "potential_stockist": row.get("potential_stockist") or "",
        "stockist_lead": row.get("stockist_lead") or "",
        "confidence": row.get("confidence") or "",
        "entity_quality": row.get("entity_quality") or "",
        "manual_review": "TRUE" if row.get("manual_review") else "FALSE",
        "discovered_by_queries": _join(row.get("discovered_by_queries") or []),
        "source_urls": _join(row.get("source_urls") or []),
        "export_evidence": evidence_text,
        "state": row.get("state") or "",
        "country": row.get("country") or "",
        "physical_store_confidence": row.get("physical_store_confidence") or "",
        "fashion_relevance": row.get("fashion_relevance") or "",
        "retail_model": row.get("retail_model") or "",
        "multi_brand_confidence": row.get("multi_brand_confidence") or "",
        "anazvara_fit_score": row.get("anazvara_fit_score") if row.get("anazvara_fit_score") not in {None, ""} else "",
        "geography_confidence": row.get("geography_confidence") or "",
        "business_confidence": row.get("business_confidence") or "",
        "discovery_source": row.get("discovery_source") or "",
        "discovery_source_url": row.get("discovery_source_url") or "",
        "discovery_source_type": row.get("discovery_source_type") or "",
        "evidence_physical_store": _join(row.get("evidence_physical_store") or []),
        "evidence_multi_brand": _join(row.get("evidence_multi_brand") or []),
        "evidence_fashion": _join(row.get("evidence_fashion") or []),
        "evidence_fit": _join(row.get("evidence_fit") or []),
        "reason": row.get("reason") or row.get("export_reason") or "",
    }


def sort_export_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda item: (
            GROUP_ORDER.get(str(item.get("export_group")), 9),
            str(item.get("business_name") or "").casefold(),
            str(item.get("website") or ""),
            str(item.get("source_url") or ""),
        ),
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(_csv_view(row))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _ambiguous_names(
    rows: list[BusinessCandidate],
    pairs: list[dict[str, Any]],
) -> set[str]:
    flagged = {str(item.get("left")) for item in pairs}
    flagged.update(str(item.get("right")) for item in pairs)
    counts: Counter[str] = Counter()
    for row in rows:
        if _has(row.business_name):
            counts[row.business_name.strip().casefold()] += 1
    for name, count in counts.items():
        if count > 1:
            flagged.add(name)
    return {item for item in flagged if item}


def export_leads(
    input_path: Path | str,
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Build deterministic CRM-ready exports from an existing benchmark."""
    source = Path(input_path)
    dest = Path(output_dir) if output_dir else source.parent
    loaded = load_benchmark(source)
    original_payloads = loaded["records"]
    originals = [business_from_dict(item) for item in original_payloads]
    unique, duplicates_merged, ambiguous_pairs = merge_business_records(originals)
    ambiguous_names = _ambiguous_names(unique, ambiguous_pairs)

    exported: list[dict[str, Any]] = []
    contradiction_records = 0
    resolved = 0
    unresolved = 0
    for row in unique:
        name_key = (row.business_name or "").strip().casefold()
        decision = assign_export_group(row, ambiguous=name_key in ambiguous_names and name_key != "")
        if decision["conflicts"]:
            contradiction_records += 1
        if decision["contradiction_resolved"]:
            resolved += 1
        if decision["contradiction_unresolved"]:
            unresolved += 1
        exported.append(build_export_row(row, decision))
    exported = sort_export_rows(exported)

    leads = [row for row in exported if row["export_group"] == GROUP_LEAD]
    reviews = [row for row in exported if row["export_group"] == GROUP_REVIEW]
    excluded = [row for row in exported if row["export_group"] == GROUP_EXCLUDED]
    exclusion_reasons = Counter(row["export_reason"] for row in excluded)
    review_reasons = Counter(row["export_reason"] for row in reviews)
    no_contact = sum(1 for row in leads if not any(
        row.get(key) for key in ("website", "instagram", "facebook", "email", "phone")
    ))

    paths = {key: dest / name for key, name in OUTPUT_FILES.items()}
    write_csv(paths["all_csv"], exported)
    write_csv(paths["leads_csv"], leads)
    write_csv(paths["review_csv"], reviews)
    write_csv(paths["excluded_csv"], excluded)
    write_json(paths["all_json"], exported)
    summary = {
        "source_path": str(source),
        "total_original_records": len(originals),
        "unique_business_candidates": len(unique),
        "lead_count": len(leads),
        "review_count": len(reviews),
        "excluded_count": len(excluded),
        "duplicates_merged": duplicates_merged,
        "contradictory_classifications_detected": contradiction_records,
        "contradictory_classifications_resolved": resolved,
        "unresolved_conflicts": unresolved,
        "ambiguous_identity_pairs": len(ambiguous_pairs),
        "leads_with_websites": sum(1 for row in leads if row.get("website")),
        "leads_with_instagram": sum(1 for row in leads if row.get("instagram")),
        "leads_with_email": sum(1 for row in leads if row.get("email")),
        "leads_with_phone": sum(1 for row in leads if row.get("phone")),
        "leads_with_no_usable_contact_path": no_contact,
        "top_exclusion_reasons": exclusion_reasons.most_common(10),
        "top_review_reasons": review_reasons.most_common(10),
        "output_files": {key: str(path) for key, path in paths.items()},
    }
    write_json(paths["summary"], summary)
    return {
        "summary": summary,
        "rows": exported,
        "paths": {key: str(path) for key, path in paths.items()},
    }


def format_lead_table(rows: list[dict[str, Any]], *, limit: int = 30) -> list[str]:
    lines = [
        f"{'BUSINESS':<28} {'CITY':<10} {'WEBSITE':<8} {'INSTAGRAM':<10} {'EMAIL':<7} GROUP"
    ]
    visible = rows[:limit]
    for row in visible:
        lines.append(
            f"{str(row.get('business_name') or 'UNKNOWN')[:28]:<28} "
            f"{str(row.get('city') or 'UNKNOWN')[:10]:<10} "
            f"{_present(row.get('website')):<8} "
            f"{_present(row.get('instagram')):<10} "
            f"{_present(row.get('email')):<7} "
            f"{row.get('export_group')}"
        )
    return lines


def print_export_report(result: dict[str, Any]) -> None:
    summary = result["summary"]
    print("=== STOCKIST LEAD EXPORT ===")
    print(f"Source: {summary['source_path']}")
    print(f"Total original records: {summary['total_original_records']}")
    print(f"Unique business candidates: {summary['unique_business_candidates']}")
    print(f"LEAD: {summary['lead_count']}")
    print(f"REVIEW: {summary['review_count']}")
    print(f"EXCLUDED: {summary['excluded_count']}")
    print(f"Duplicates merged: {summary['duplicates_merged']}")
    print(
        "Contradictory classifications detected: "
        f"{summary['contradictory_classifications_detected']}"
    )
    print(
        "Contradictory classifications resolved: "
        f"{summary['contradictory_classifications_resolved']}"
    )
    print(f"Unresolved conflicts: {summary['unresolved_conflicts']}")
    print(f"Leads with websites: {summary['leads_with_websites']}")
    print(f"Leads with Instagram: {summary['leads_with_instagram']}")
    print(f"Leads with email: {summary['leads_with_email']}")
    print(f"Leads with phone: {summary['leads_with_phone']}")
    print(
        "Leads with no usable contact path: "
        f"{summary['leads_with_no_usable_contact_path']}"
    )
    print()
    print("Top exclusion reasons:")
    if not summary["top_exclusion_reasons"]:
        print("  (none)")
    for reason, count in summary["top_exclusion_reasons"]:
        print(f"  {count}  {reason}")
    print("Top review reasons:")
    if not summary["top_review_reasons"]:
        print("  (none)")
    for reason, count in summary["top_review_reasons"]:
        print(f"  {count}  {reason}")
    print()
    print(f"CSV: {summary['output_files']['all_csv']}")
    print(f"JSON: {summary['output_files']['all_json']}")
    print(f"Summary: {summary['output_files']['summary']}")
    print()
    print("=== LEAD BUSINESSES ===")
    leads = [row for row in result["rows"] if row["export_group"] == GROUP_LEAD]
    if not leads:
        print("(none)")
        return
    for line in format_lead_table(leads):
        print(line)
    if len(leads) > 30:
        print(f"... {len(leads) - 30} more in {summary['output_files']['leads_csv']}")
