"""Lead-generation layer for manual stockist review.

Does not replace Qwen's potential_stockist. Does not hard-code businesses.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from business_candidates import UNKNOWN, BusinessCandidate, BusinessType
from content_extraction import BusinessSignals, PageEvidence
from enrichment import EnrichedEvidence

YES_NO_UNKNOWN = {"YES", "NO", "UNKNOWN"}
RETAIL_TYPES = {
    "BOUTIQUE",
    "MULTI_DESIGNER",
    "MULTI_BRAND",
    "CONCEPT_STORE",
    "RETAILER",
    "FASHION_RETAILER",
}
LEAD_RELEVANCE_RE = re.compile(
    r"\b(fashion\s+boutique|clothing\s+boutique|designer\s+boutique|"
    r"women'?s\s+boutique|boutique|concept\s+store|curated\s+store|"
    r"curated\s+(?:lifestyle\s+)?(?:fashion\s+)?(?:collections?|selection)|"
    r"multi[\s\-]?brand|multi[\s\-]?designer|independent\s+designers|"
    r"fashion\s+retailer|clothing\s+store|fashion\s+store|"
    r"lifestyle\s+store|fashion\s+collections?|women'?s\s+clothing|"
    r"luxury\s+clothing)\b",
    re.IGNORECASE,
)
LEAD_NO_RE = re.compile(
    r"\b(manufacturer|wholesaler|wholesale\s+only|fashion\s+school|"
    r"photography|photographer|stylist|personal\s+shopper|"
    r"marketplace|fashion\s+blog|magazine)\b",
    re.IGNORECASE,
)
OWN_LABEL_RE = re.compile(
    r"\b(own[\s\-]?label|own[\s\-]?brand|our own collection|designer studio|"
    r"we design|only our (?:own )?brand)\b",
    re.IGNORECASE,
)
DESIGNER_STORE_RE = re.compile(
    r"\b(women designer store|designer store|designer wear)\b",
    re.IGNORECASE,
)


def _text_blob(page: PageEvidence | None, extra: str = "") -> str:
    if page is None:
        return extra
    return " ".join(
        part
        for part in (
            extra,
            page.title,
            page.og_site_name,
            page.meta_description,
            " ".join(page.headings or []),
            page.text or "",
        )
        if part
    )


def contact_paths(row: BusinessCandidate) -> dict[str, str | None]:
    evidence = row.evidence or {}
    return {
        "website": row.website,
        "instagram": row.instagram,
        "facebook": row.facebook,
        "email": row.email,
        "phone": row.phone,
        "google_maps_url": evidence.get("google_maps_url"),
        "address": row.address,
    }


def _contactable(paths: dict[str, str | None]) -> bool:
    return any(
        paths.get(key)
        for key in ("website", "instagram", "facebook", "email", "phone", "google_maps_url")
    )


def _source_is_discovery(row: BusinessCandidate) -> bool:
    signals = list((row.evidence or {}).get("signals") or [])
    if row.source_type in {"DIRECTORY", "ARTICLE"}:
        return True
    return any(item in signals for item in ("roundup_page", "publisher_not_boutique"))


def _own_label_only(row: BusinessCandidate, text: str) -> bool:
    evidence = row.evidence or {}
    validated = (evidence.get("ai") or {}).get("validated") or {}
    positioning = str(validated.get("designer_positioning") or "").upper()
    carries = str(validated.get("carries_other_brands") or "").upper()
    if carries == "YES":
        return False
    if LEAD_RELEVANCE_RE.search(text) and positioning != "OWN_LABEL":
        return False
    if positioning == "OWN_LABEL":
        return True
    if OWN_LABEL_RE.search(text) or DESIGNER_STORE_RE.search(text):
        return True
    rejected = list(validated.get("rejected") or [])
    return any(item in rejected for item in ("own_label_not_stockist", "designer_store_not_stockist"))


def _fashion_or_retail(row: BusinessCandidate, text: str) -> bool:
    evidence = row.evidence or {}
    ai_type = str(evidence.get("ai_business_type") or "").upper()
    if row.business_type.value in RETAIL_TYPES or ai_type in RETAIL_TYPES:
        return True
    if str(evidence.get("ai_potential_stockist") or "").upper() == "YES":
        return True
    return bool(LEAD_RELEVANCE_RE.search(text))


def assess_stockist_lead(
    row: BusinessCandidate,
    page_evidence: PageEvidence | None = None,
    signals: BusinessSignals | None = None,
    enriched: EnrichedEvidence | None = None,
) -> dict[str, Any]:
    """Return a lead decision. Never writes potential_stockist."""
    del signals, enriched
    text = _text_blob(page_evidence, extra=row.business_name or "")
    paths = contact_paths(row)
    reasons: list[str] = []
    if _source_is_discovery(row):
        return {
            "stockist_lead": "NO",
            "manual_review": False,
            "reasons": ["discovery_page_not_lead"],
            "contacts": paths,
        }
    if row.business_type is BusinessType.MARKETPLACE or LEAD_NO_RE.search(text):
        reasons.append("not_a_retail_prospect")
        return {
            "stockist_lead": "NO",
            "manual_review": False,
            "reasons": reasons,
            "contacts": paths,
        }
    if _own_label_only(row, text):
        return {
            "stockist_lead": "NO",
            "manual_review": False,
            "reasons": ["own_label_not_lead"],
            "contacts": paths,
        }
    named = row.business_name not in {None, "", UNKNOWN}
    if not named and not _contactable(paths):
        return {
            "stockist_lead": "UNKNOWN",
            "manual_review": False,
            "reasons": ["identity_insufficient"],
            "contacts": paths,
        }
    relevant = _fashion_or_retail(row, text)
    if not relevant:
        return {
            "stockist_lead": "UNKNOWN",
            "manual_review": False,
            "reasons": ["fashion_retail_evidence_missing"],
            "contacts": paths,
        }
    if not _contactable(paths):
        return {
            "stockist_lead": "UNKNOWN",
            "manual_review": False,
            "reasons": ["no_usable_contact_path"],
            "contacts": paths,
        }
    if LEAD_RELEVANCE_RE.search(text):
        reasons.append("fashion_retail_page_evidence")
    if paths.get("website"):
        reasons.append("official_website")
    if any(paths.get(key) for key in ("instagram", "facebook", "email", "phone", "google_maps_url")):
        reasons.append("contactable")
    if not paths.get("address"):
        reasons.append("address_not_required")
    return {
        "stockist_lead": "YES",
        "manual_review": True,
        "reasons": reasons,
        "contacts": paths,
    }


def attach_stockist_lead(
    row: BusinessCandidate,
    page_evidence: PageEvidence | None = None,
    signals: BusinessSignals | None = None,
    enriched: EnrichedEvidence | None = None,
) -> BusinessCandidate:
    """Store stockist_lead next to Qwen fields. Does not change potential_stockist."""
    result = assess_stockist_lead(row, page_evidence, signals, enriched)
    evidence = dict(row.evidence or {})
    evidence["stockist_lead"] = result["stockist_lead"]
    evidence["manual_review"] = result["manual_review"]
    evidence["stockist_lead_reasons"] = list(result["reasons"])
    return replace(row, evidence=evidence)


def lead_of(row: BusinessCandidate | None) -> str:
    if row is None:
        return "UNKNOWN"
    value = str((row.evidence or {}).get("stockist_lead") or "UNKNOWN").upper()
    return value if value in YES_NO_UNKNOWN else "UNKNOWN"
