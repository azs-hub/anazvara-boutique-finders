"""Benchmark-only Qwen inspection for valid SELF businesses.

Does not change the prompt, validation, or production classification.
"""

from __future__ import annotations

import re
from typing import Any

from business_candidates import BusinessCandidate
from local_llm import OFFICIAL_SITE_STOCKIST_RE, STOCKIST_YES_RE
from stockist_lead import lead_of

YES_NO_UNKNOWN = {"YES", "NO", "UNKNOWN"}
CONCEPT_OR_CURATED_RE = re.compile(
    r"\b(concept\s+store|curated|multi[\s\-]?brand|multi[\s\-]?designer|"
    r"independent\s+(?:designers?|brands?|labels?))\b",
    re.IGNORECASE,
)
OWN_LABEL_HINT_RE = re.compile(
    r"\b(own[\s\-]?label|own[\s\-]?brand|our own collection|designer studio|"
    r"we design)\b",
    re.IGNORECASE,
)
FASHION_HINT_RE = re.compile(
    r"\b(boutique|fashion|clothing|womenswear|women'?s\s+wear|apparel)\b",
    re.IGNORECASE,
)


def _has(value: Any) -> bool:
    return bool(value) and value != "UNKNOWN"


def _payload_text(payload: dict | None) -> str:
    if not payload:
        return ""
    return " ".join(
        part
        for part in (
            payload.get("text_excerpt"),
            payload.get("page_title"),
            payload.get("meta_description"),
            " ".join(payload.get("headings") or []),
            " ".join(payload.get("addresses") or []),
        )
        if part
    )


def stockist_evidence_available(payload: dict | None) -> bool:
    text = _payload_text(payload)
    return bool(STOCKIST_YES_RE.search(text) or OFFICIAL_SITE_STOCKIST_RE.search(text))


def evidence_sufficient_label(payload: dict | None) -> str:
    text = _payload_text(payload)
    if stockist_evidence_available(payload):
        return "YES"
    if CONCEPT_OR_CURATED_RE.search(text) or FASHION_HINT_RE.search(text):
        return "PARTIAL"
    if len(text.strip()) < 80:
        return "NO"
    return "PARTIAL"


def diagnose_qwen(row: BusinessCandidate, inspection: dict) -> str:
    """A saw enough / B saw too little / C extraction incomplete / D genuine non-stockist."""
    stockist = str((row.evidence or {}).get("ai_potential_stockist") or "UNKNOWN").upper()
    payload = inspection.get("payload")
    text = _payload_text(payload)
    validated = ((row.evidence or {}).get("ai") or {}).get("validated") or {}
    positioning = str(validated.get("designer_positioning") or "").upper()
    carries = str(validated.get("carries_other_brands") or "").upper()
    if not inspection.get("attempted"):
        skip = inspection.get("skipped_reason") or "not_sent"
        if skip in {"insufficient_page_evidence", "roundup_without_page_text"}:
            return "C"
        if skip in {"source_not_website", "source_not_official_identity"}:
            return "B"
        return "B"
    if stockist == "YES":
        return "intended"
    if positioning == "OWN_LABEL" or carries == "NO" or OWN_LABEL_HINT_RE.search(text):
        return "D"
    if stockist_evidence_available(payload) or CONCEPT_OR_CURATED_RE.search(text):
        return "A"
    if not (payload or {}).get("text_excerpt"):
        return "C"
    if len(text.strip()) < 80 or not FASHION_HINT_RE.search(text):
        return "B"
    return "C"


def concrete_reason(row: BusinessCandidate, inspection: dict) -> str:
    validated = ((row.evidence or {}).get("ai") or {}).get("validated") or {}
    positioning = str(validated.get("designer_positioning") or "").upper()
    carries = str(validated.get("carries_other_brands") or "").upper()
    ai_type = str((row.evidence or {}).get("ai_business_type") or "").upper()
    payload = inspection.get("payload") or {}
    text = _payload_text(payload)
    if not inspection.get("attempted"):
        return f"qwen_not_sent:{inspection.get('skipped_reason') or 'unknown'}"
    if positioning == "OWN_LABEL":
        return "own-label only"
    if OWN_LABEL_HINT_RE.search(text) and carries != "YES":
        return "designer studio / own-label language"
    if carries == "NO":
        return "insufficient multi-brand evidence"
    if ai_type in {"DESIGNER", "BRAND"} and carries != "YES":
        return "ambiguous business type"
    if not FASHION_HINT_RE.search(text):
        return "insufficient fashion evidence"
    if not stockist_evidence_available(payload):
        return "insufficient multi-brand evidence"
    return "Qwen classified NO/UNKNOWN despite stockist-style language"


def is_valid_goa_self(row: BusinessCandidate) -> bool:
    evidence = row.evidence or {}
    return (
        evidence.get("entity_relationship") == "SELF"
        and evidence.get("entity_is_business") == "YES"
        and evidence.get("geographic_relevance") == "YES"
    )


def qwen_card(row: BusinessCandidate) -> dict[str, Any]:
    evidence = row.evidence or {}
    inspection = dict(evidence.get("qwen_inspection") or {})
    validated = (evidence.get("ai") or {}).get("validated") or {}
    payload = inspection.get("payload") or {}
    stockist = str(evidence.get("ai_potential_stockist") or "UNKNOWN").upper()
    if stockist not in YES_NO_UNKNOWN:
        stockist = "UNKNOWN"
    diagnosis = diagnose_qwen(row, inspection)
    return {
        "business_name": row.business_name,
        "website": row.website,
        "instagram": row.instagram,
        "facebook": row.facebook,
        "geographic_relevance": evidence.get("geographic_relevance"),
        "business_context": evidence.get("business_context"),
        "entity_quality": evidence.get("entity_quality"),
        "women_fashion": evidence.get("ai_women_fashion") or row.women_fashion_relevance.value,
        "business_type": evidence.get("ai_business_type") or row.business_type.value,
        "physical_store": evidence.get("ai_physical_store") or row.physical_store.value,
        "carries_other_brands": validated.get("carries_other_brands"),
        "potential_stockist": stockist,
        "ai_confidence": evidence.get("ai_confidence"),
        "qwen_evidence": list(validated.get("evidence") or []),
        "qwen_raw": inspection.get("raw"),
        "stockist_evidence_available": stockist_evidence_available(payload),
        "stockist_lead": lead_of(row),
        "manual_review": bool(evidence.get("manual_review")),
        "qwen_attempted": bool(inspection.get("attempted")),
        "qwen_skipped_reason": inspection.get("skipped_reason"),
        "pages_sent": list(inspection.get("pages") or []),
        "payload": payload,
        "concept_or_curated_in_payload": bool(CONCEPT_OR_CURATED_RE.search(_payload_text(payload))),
        "evidence_sufficient": evidence_sufficient_label(payload),
        "diagnosis": diagnosis,
        "concrete_reason": concrete_reason(row, inspection),
        "validation_reasons": list(validated.get("validation_reasons") or []),
        "rejected": list(validated.get("rejected") or []),
    }


def build_qwen_inspection(rows: list[BusinessCandidate]) -> dict[str, Any]:
    cards = [qwen_card(row) for row in rows if is_valid_goa_self(row)]
    return {
        "valid_goa_self_businesses": len(cards),
        "qwen_yes": sum(1 for item in cards if item["potential_stockist"] == "YES"),
        "qwen_no": sum(1 for item in cards if item["potential_stockist"] == "NO"),
        "qwen_unknown": sum(1 for item in cards if item["potential_stockist"] == "UNKNOWN"),
        "businesses": cards,
    }


def print_qwen_inspection(block: dict[str, Any]) -> None:
    print("=== QWEN ON VALID GOA SELF BUSINESSES ===")
    print(f"valid Goa SELF businesses: {block['valid_goa_self_businesses']}")
    print(
        f"potential_stockist YES/NO/UNKNOWN: "
        f"{block['qwen_yes']} / {block['qwen_no']} / {block['qwen_unknown']}"
    )
    print()
    print(
        f"{'Business':<28} {'Stockist':<9} {'Carries':<9} {'Conf':<6} "
        f"{'Enough?':<9} Diagnosis"
    )
    for item in block["businesses"]:
        print(
            f"{str(item['business_name'])[:27]:<28} "
            f"{item['potential_stockist']:<9} "
            f"{str(item.get('carries_other_brands') or '—'):<9} "
            f"{str(item.get('ai_confidence') or '—'):<6} "
            f"{item['evidence_sufficient']:<9} "
            f"{item['diagnosis']}"
        )
    print()
    print("Diagnosis: A=saw enough, misclassified; B=too little sent; "
          "C=extraction incomplete; D=genuinely not a stockist; intended=YES with evidence")
    print()
    for item in block["businesses"]:
        print(f"--- {item['business_name']} ---")
        print(
            f"  website={item['website'] or '—'} instagram={item['instagram'] or '—'} "
            f"facebook={item['facebook'] or '—'}"
        )
        print(
            f"  geo={item['geographic_relevance']} context={item['business_context']} "
            f"quality={item['entity_quality']} lead={item['stockist_lead']} "
            f"review={item['manual_review']}"
        )
        print(
            f"  women={item['women_fashion']} type={item['business_type']} "
            f"store={item['physical_store']} carries={item['carries_other_brands']} "
            f"stockist={item['potential_stockist']} ai_conf={item['ai_confidence']}"
        )
        print(f"  stockist_evidence_available={item['stockist_evidence_available']}")
        print(f"  pages_sent={item['pages_sent'] or '—'}")
        print(f"  qwen_attempted={item['qwen_attempted']} skipped={item['qwen_skipped_reason']}")
        print(f"  concept/curated in payload={item['concept_or_curated_in_payload']}")
        print(f"  qwen evidence={item['qwen_evidence']}")
        if item["potential_stockist"] != "YES":
            print(f"  concrete_reason={item['concrete_reason']}")
            print(f"  diagnosis={item['diagnosis']} enough={item['evidence_sufficient']}")
            payload = item.get("payload") or {}
            print(f"  payload title={payload.get('page_title')}")
            print(f"  payload headings={payload.get('headings')}")
            excerpt = str(payload.get("text_excerpt") or "")
            print(f"  payload excerpt ({len(excerpt)} chars)={excerpt[:700]}")
            if item.get("qwen_raw"):
                print(f"  qwen raw stockist={item['qwen_raw'].get('potential_stockist')}")
                print(f"  qwen raw carries={item['qwen_raw'].get('carries_other_brands')}")
                print(f"  qwen raw type={item['qwen_raw'].get('business_type')}")
                print(f"  qwen raw evidence={item['qwen_raw'].get('evidence')}")
        elif item.get("qwen_raw"):
            print(f"  qwen raw stockist={item['qwen_raw'].get('potential_stockist')}")
            print(f"  qwen raw carries={item['qwen_raw'].get('carries_other_brands')}")
            print(f"  qwen raw evidence={item['qwen_raw'].get('evidence')}")
        print()
