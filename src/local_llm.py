"""Optional local Qwen classification via Ollama.

Does not replace the rule engine. Called only after
``identify_business_candidates`` for ambiguous WEBSITE records.

Rule fields stay on the BusinessCandidate. Qwen output is stored under
``evidence['ai_*']`` / ``evidence['ai']``. Validated UNKNOWN-only fills are
applied to a *copy* for comparison (benchmark path B).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, replace
from typing import Any

import requests

from business_candidates import (
    UNKNOWN,
    BusinessCandidate,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
)
from candidates import Candidate
from classification import ResultType
from content_extraction import CITY_NAMES, BusinessSignals, PageEvidence
from enrichment import EnrichedEvidence, classify_internal_page
from structured_evidence import (
    PHYSICAL_NO_RE,
    PHYSICAL_YES_RE,
    PINCODE_RE,
    clean_business_name,
    looks_generic_name,
)

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT_SECONDS = 90.0
MAX_TEXT_CHARS = 1_500
MAX_HEADINGS = 8
MAX_FACTS = 20
MAX_SIGNAL_ITEMS = 8

AI_BUSINESS_TYPES = {
    "MULTI_BRAND",
    "MULTI_DESIGNER",
    "BOUTIQUE",
    "DESIGNER",
    "CONCEPT_STORE",
    "FASHION_RETAILER",
    "OWN_BRAND",
    "DEPARTMENT_STORE",
    "WHOLESALE",
    "OTHER",
    "UNKNOWN",
}
# Rule fills use exact BusinessType members only. AI-only labels
# (MULTI_BRAND, CONCEPT_STORE, OWN_BRAND, FASHION_RETAILER, …) stay on
# evidence['ai_business_type'] and are never aliased into nearby rule enums.
RELEVANCE_VALUES = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
PHYSICAL_VALUES = {"YES", "NO", "UNKNOWN"}
YES_NO_UNKNOWN = {"YES", "NO", "UNKNOWN"}
PRICE_VALUES = {"BUDGET", "MID_RANGE", "PREMIUM", "LUXURY", "UNKNOWN"}
STOCKIST_VALUES = {"HIGH", "MEDIUM", "LOW", "NO", "UNKNOWN"}
DESIGNER_POSITIONING = {
    "MULTI_DESIGNER",
    "DESIGNER_FOCUSED",
    "MULTI_BRAND",
    "INDEPENDENT_BOUTIQUE",
    "GENERAL_FASHION",
    "OWN_LABEL",
    "UNKNOWN",
}
SUSTAINABILITY_HINT_RE = re.compile(
    r"\b(sustainab|eco[\s\-]?friendly|organic|ethical|upcycled|slow\s+fashion)\b",
    re.IGNORECASE,
)
PRICE_HINT_RE = re.compile(
    r"(?:₹|rs\.?|inr|\$|usd|eur|gbp)\s*[\d,]+|[\d,]+\s*(?:₹|rs\.?|inr)|"
    r"\b(price\s+range|starting\s+at|from\s+rs)\b",
    re.IGNORECASE,
)
BANNED_NAMES = {
    "visit site",
    "visit website",
    "click here",
    "read more",
    "learn more",
    "shop now",
    "view store",
}
LOCAL_BUSINESS_TYPES = {
    "localbusiness",
    "store",
    "clothingstore",
    "fashionstore",
    "shoestore",
    "jewelrystore",
}

SYSTEM_PROMPT = """You classify fashion businesses from extracted website evidence.
Return JSON only. Do not invent facts. Use UNKNOWN when evidence is missing.
Organization or HQ address alone is not a physical store.
Directory, article, and listicle pages are not the business itself.
Never use names like Visit Site, Cart, Checkout, or Order Summary.
Do not invent a price range or sustainability claim.
"""


@dataclass
class LLMCallResult:
    """Outcome of one optional Ollama call. Failures never raise."""

    enabled: bool
    attempted: bool
    skipped_reason: str | None
    error: str | None
    raw: dict | None
    validated: dict | None
    elapsed_seconds: float


def ollama_enabled(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    return os.environ.get("OLLAMA_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ollama_url() -> str:
    return os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")


def ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def ollama_timeout() -> float:
    raw = os.environ.get("OLLAMA_TIMEOUT_SECONDS", "")
    try:
        return max(1.0, float(raw)) if raw else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def is_rules_confident(row: BusinessCandidate) -> bool:
    """True when the rule engine already has enough to skip Qwen."""
    name_ok = row.business_name != UNKNOWN and str(
        (row.evidence or {}).get("name_confidence") or ""
    ) in {"HIGH", "MEDIUM"}
    return (
        row.confidence is Confidence.HIGH
        and name_ok
        and row.business_type is not BusinessType.UNKNOWN
        and row.women_fashion_relevance is not Relevance.UNKNOWN
        and row.physical_store is not PhysicalStore.UNKNOWN
    )


def ambiguity_reason(
    row: BusinessCandidate,
    candidate: Candidate,
    page_evidence: PageEvidence,
) -> str | None:
    """Return a skip reason, or None if this record should be sent to Qwen."""
    if candidate.result_type is not ResultType.WEBSITE:
        return "source_not_website"
    if row.source_type in {"DIRECTORY", "ARTICLE", "SOCIAL", "VIDEO"}:
        return "source_not_website"
    signals = list((row.evidence or {}).get("signals") or [])
    if "publisher_not_boutique" in signals and not (page_evidence.text or "").strip():
        return "roundup_without_page_text"
    if not (page_evidence.text or page_evidence.title or page_evidence.structured_facts):
        return "insufficient_page_evidence"
    if is_rules_confident(row):
        return "rules_already_confident"
    return None


def build_llm_payload(
    *,
    candidate: Candidate,
    row: BusinessCandidate,
    page_evidence: PageEvidence,
    signals: BusinessSignals,
    enriched: EnrichedEvidence | None = None,
) -> dict[str, Any]:
    """Compact evidence for Qwen. Never send raw HTML."""
    facts = list(page_evidence.structured_facts)
    roles: list[str] = []
    if enriched is not None:
        for page in enriched.pages:
            facts.extend(page.structured_facts)
            roles.append(
                classify_internal_page(
                    page.final_url or page.source_url, root_url=enriched.root_url
                )
            )
    compact_facts = []
    seen: set[tuple[str, str, str]] = set()
    for fact in facts:
        key = (fact.field, fact.source, fact.value)
        if key in seen:
            continue
        seen.add(key)
        compact_facts.append(
            {
                "field": fact.field,
                "value": fact.value[:180],
                "source": fact.source,
                "schema_type": fact.schema_type,
            }
        )
        if len(compact_facts) >= MAX_FACTS:
            break
    return {
        "search_query": candidate.search_query,
        "source_type": row.source_type,
        "source_url": row.source_url,
        "website": row.website,
        "page_title": page_evidence.title,
        "og_site_name": page_evidence.og_site_name,
        "meta_description": (page_evidence.meta_description or "")[:300] or None,
        "headings": list(page_evidence.headings[:MAX_HEADINGS]),
        "structured_facts": compact_facts,
        "emails": list(signals.emails[:MAX_SIGNAL_ITEMS]),
        "phones": list(signals.phones[:MAX_SIGNAL_ITEMS]),
        "social": list(signals.social_urls[:MAX_SIGNAL_ITEMS]),
        "addresses": list(signals.address_candidates[:MAX_SIGNAL_ITEMS]),
        "city_mentions": list(signals.city_mentions[:MAX_SIGNAL_ITEMS]),
        "enrichment_roles": list(dict.fromkeys(roles)),
        "text_excerpt": (page_evidence.text or "")[:MAX_TEXT_CHARS],
        "rule_result": {
            "business_name": row.business_name,
            "business_type": row.business_type.value,
            "women_fashion": row.women_fashion_relevance.value,
            "physical_store": row.physical_store.value,
            "city": row.city,
            "confidence": row.confidence.value,
            "name_source": (row.evidence or {}).get("name_source"),
            "signals": list((row.evidence or {}).get("signals") or [])[:12],
        },
    }


def empty_validated() -> dict[str, Any]:
    return {
        "is_business": None,
        "business_name": UNKNOWN,
        "business_type": "UNKNOWN",
        "women_fashion": "UNKNOWN",
        "physical_store": "UNKNOWN",
        "city": UNKNOWN,
        "carries_other_brands": "UNKNOWN",
        "designer_positioning": "UNKNOWN",
        "sustainability_focus": "UNKNOWN",
        "price_positioning": "UNKNOWN",
        "observed_price_range": "UNKNOWN",
        "style_fit": "UNKNOWN",
        "potential_stockist": "UNKNOWN",
        "confidence": "LOW",
        "evidence": [],
        "rejected": [],
    }


def validate_llm_result(
    raw: dict | None,
    payload: dict[str, Any],
    row: BusinessCandidate,
) -> dict[str, Any]:
    """Deterministic checks. Hallucinated or conflicting AI values become UNKNOWN."""
    validated = empty_validated()
    rejected: list[str] = []
    if not isinstance(raw, dict):
        validated["rejected"] = ["invalid_json"]
        return validated

    text_blob = " ".join(
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
    signals = list((payload.get("rule_result") or {}).get("signals") or [])
    source_type = str(payload.get("source_type") or "")

    is_business = raw.get("is_business")
    if not isinstance(is_business, bool):
        is_business = None
        rejected.append("is_business_not_bool")
    if source_type in {"DIRECTORY", "ARTICLE"}:
        is_business = False
        rejected.append("directory_or_article_not_business")
    if any(item in signals for item in ("roundup_page", "publisher_not_boutique")):
        is_business = False
        rejected.append("roundup_or_publisher")
    validated["is_business"] = is_business

    name = clean_business_name(str(raw.get("business_name") or ""))
    if name != UNKNOWN and looks_generic_name(name):
        rejected.append("generic_ai_name")
        name = UNKNOWN
    if name != UNKNOWN and name.strip().lower() in BANNED_NAMES:
        rejected.append("banned_ai_name")
        name = UNKNOWN
    validated["business_name"] = name

    ai_type = _enum_value(raw.get("business_type"), AI_BUSINESS_TYPES, "UNKNOWN")
    validated["business_type"] = ai_type

    women = _enum_value(raw.get("women_fashion"), RELEVANCE_VALUES, "UNKNOWN")
    validated["women_fashion"] = women

    physical = _enum_value(raw.get("physical_store"), PHYSICAL_VALUES, "UNKNOWN")
    if row.physical_store is PhysicalStore.NO:
        if physical == "YES":
            rejected.append("cannot_override_online_only")
        physical = "NO"
    elif PHYSICAL_NO_RE.search(text_blob) and physical == "YES":
        rejected.append("online_only_language")
        physical = "NO"
    elif physical == "YES" and not _has_storefront_evidence(payload, text_blob):
        rejected.append("physical_yes_without_storefront")
        physical = "UNKNOWN"
    validated["physical_store"] = physical

    ai_city = str(raw.get("city") or "").strip() or UNKNOWN
    if ai_city != UNKNOWN:
        allowed = _allowed_cities(payload, text_blob)
        if ai_city.lower() not in {item.lower() for item in allowed}:
            rejected.append("city_not_in_evidence")
            ai_city = UNKNOWN
        elif row.city != UNKNOWN and ai_city.lower() != row.city.lower():
            rejected.append("city_mismatch_with_rules")
            ai_city = UNKNOWN
    validated["city"] = ai_city

    validated["carries_other_brands"] = _enum_value(
        raw.get("carries_other_brands"), YES_NO_UNKNOWN, "UNKNOWN"
    )
    validated["designer_positioning"] = _enum_value(
        raw.get("designer_positioning"), DESIGNER_POSITIONING, "UNKNOWN"
    )

    sustainability = _enum_value(
        raw.get("sustainability_focus"), RELEVANCE_VALUES, "UNKNOWN"
    )
    if sustainability in {"HIGH", "MEDIUM"} and not SUSTAINABILITY_HINT_RE.search(text_blob):
        rejected.append("sustainability_without_evidence")
        sustainability = "UNKNOWN"
    validated["sustainability_focus"] = sustainability

    price = _enum_value(raw.get("price_positioning"), PRICE_VALUES, "UNKNOWN")
    observed = str(raw.get("observed_price_range") or "UNKNOWN").strip() or "UNKNOWN"
    if observed.upper() == "UNKNOWN":
        observed = "UNKNOWN"
    if (price != "UNKNOWN" or observed != "UNKNOWN") and not PRICE_HINT_RE.search(
        text_blob
    ):
        rejected.append("price_without_evidence")
        price = "UNKNOWN"
        observed = "UNKNOWN"
    validated["price_positioning"] = price
    validated["observed_price_range"] = observed

    validated["style_fit"] = _enum_value(raw.get("style_fit"), RELEVANCE_VALUES, "UNKNOWN")
    validated["potential_stockist"] = _enum_value(
        raw.get("potential_stockist"), STOCKIST_VALUES, "UNKNOWN"
    )
    validated["confidence"] = _enum_value(raw.get("confidence"), {"HIGH", "MEDIUM", "LOW"}, "LOW")
    evidence_items = raw.get("evidence")
    if isinstance(evidence_items, list):
        validated["evidence"] = [str(item)[:200] for item in evidence_items[:12]]
    validated["rejected"] = rejected
    return validated


def attach_llm_result(row: BusinessCandidate, result: LLMCallResult) -> BusinessCandidate:
    """Store rule snapshots and AI fields. Does not change rule classifications."""
    evidence = dict(row.evidence or {})
    evidence["rule_business_type"] = row.business_type.value
    evidence["rule_women_fashion"] = row.women_fashion_relevance.value
    evidence["rule_physical_store"] = row.physical_store.value
    evidence["rule_city"] = row.city
    evidence["rule_business_name"] = row.business_name
    evidence["rule_confidence"] = row.confidence.value
    validated = result.validated or empty_validated()
    evidence["ai_is_business"] = validated.get("is_business")
    evidence["ai_business_type"] = validated.get("business_type")
    evidence["ai_women_fashion"] = validated.get("women_fashion")
    evidence["ai_physical_store"] = validated.get("physical_store")
    evidence["ai_city"] = validated.get("city")
    evidence["ai_business_name"] = validated.get("business_name")
    evidence["ai_confidence"] = validated.get("confidence")
    evidence["ai"] = {
        "enabled": result.enabled,
        "attempted": result.attempted,
        "skipped_reason": result.skipped_reason,
        "error": result.error,
        "elapsed_seconds": result.elapsed_seconds,
        "raw": result.raw,
        "validated": validated,
    }
    return replace(row, evidence=evidence)


def apply_unknown_fills(row: BusinessCandidate) -> BusinessCandidate:
    """Path B: fill UNKNOWN rule fields from validated AI. Never override known rules."""
    validated = ((row.evidence or {}).get("ai") or {}).get("validated") or {}
    if not validated:
        return row
    name = row.business_name
    if name == UNKNOWN and validated.get("business_name") not in {None, "", UNKNOWN}:
        name = str(validated["business_name"])
    business_type = row.business_type
    if business_type is BusinessType.UNKNOWN:
        mapped = _exact_rule_business_type(validated.get("business_type"))
        if mapped is not None:
            business_type = mapped
    women = row.women_fashion_relevance
    if women is Relevance.UNKNOWN:
        women_value = validated.get("women_fashion")
        if women_value in RELEVANCE_VALUES and women_value != "UNKNOWN":
            women = Relevance(women_value)
    physical = row.physical_store
    if physical is PhysicalStore.UNKNOWN:
        physical_value = validated.get("physical_store")
        if physical_value in PHYSICAL_VALUES and physical_value != "UNKNOWN":
            physical = PhysicalStore(physical_value)
    city = row.city
    if city == UNKNOWN and validated.get("city") not in {None, "", UNKNOWN}:
        city = str(validated["city"])
    return replace(
        row,
        business_name=name,
        business_type=business_type,
        women_fashion_relevance=women,
        physical_store=physical,
        city=city,
    )


class LocalLLMClient:
    """HTTP client for a local Ollama server. Network failures are recorded."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.enabled = ollama_enabled(enabled)
        self.base_url = (base_url or ollama_url()).rstrip("/")
        self.model = model or ollama_model()
        self.timeout = timeout if timeout is not None else ollama_timeout()
        self.session = session or requests.Session()

    def classify(
        self,
        *,
        candidate: Candidate,
        row: BusinessCandidate,
        page_evidence: PageEvidence,
        signals: BusinessSignals,
        enriched: EnrichedEvidence | None = None,
    ) -> LLMCallResult:
        if not self.enabled:
            return LLMCallResult(
                enabled=False,
                attempted=False,
                skipped_reason="disabled",
                error=None,
                raw=None,
                validated=None,
                elapsed_seconds=0.0,
            )
        skip = ambiguity_reason(row, candidate, page_evidence)
        if skip:
            return LLMCallResult(
                enabled=True,
                attempted=False,
                skipped_reason=skip,
                error=None,
                raw=None,
                validated=None,
                elapsed_seconds=0.0,
            )
        payload = build_llm_payload(
            candidate=candidate,
            row=row,
            page_evidence=page_evidence,
            signals=signals,
            enriched=enriched,
        )
        started = time.monotonic()
        try:
            raw = self._chat(payload)
        except requests.Timeout:
            return LLMCallResult(
                enabled=True,
                attempted=True,
                skipped_reason=None,
                error=f"Timed out after {self.timeout}s",
                raw=None,
                validated=None,
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
        except requests.RequestException as exc:
            return LLMCallResult(
                enabled=True,
                attempted=True,
                skipped_reason=None,
                error=f"Ollama request failed: {exc}",
                raw=None,
                validated=None,
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return LLMCallResult(
                enabled=True,
                attempted=True,
                skipped_reason=None,
                error=f"Invalid JSON from Ollama: {exc}",
                raw=None,
                validated=None,
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
        validated = validate_llm_result(raw, payload, row)
        return LLMCallResult(
            enabled=True,
            attempted=True,
            skipped_reason=None,
            error=None,
            raw=raw,
            validated=validated,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )

    def _chat(self, payload: dict[str, Any]) -> dict:
        response = self.session.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                "stream": False,
                "format": "json",
                "think": False,
                "options": {"temperature": 0},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        content = ""
        message = body.get("message") if isinstance(body, dict) else None
        if isinstance(message, dict):
            content = str(message.get("content") or "")
        elif isinstance(body, dict):
            content = str(body.get("response") or "")
        content = _strip_think(content)
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("Ollama JSON was not an object")
        return parsed


def maybe_classify_with_local_llm(
    client: LocalLLMClient,
    *,
    candidate: Candidate,
    row: BusinessCandidate,
    page_evidence: PageEvidence,
    signals: BusinessSignals,
    enriched: EnrichedEvidence | None = None,
) -> tuple[BusinessCandidate, LLMCallResult]:
    """Attach Qwen output to one rule-identified row. Safe if Ollama is down."""
    result = client.classify(
        candidate=candidate,
        row=row,
        page_evidence=page_evidence,
        signals=signals,
        enriched=enriched,
    )
    return attach_llm_result(row, result), result


def _exact_rule_business_type(value: Any) -> BusinessType | None:
    """Return a rule enum only when the AI label is already a BusinessType."""
    text = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    try:
        mapped = BusinessType(text)
    except ValueError:
        return None
    if mapped is BusinessType.UNKNOWN:
        return None
    return mapped


def _enum_value(value: Any, allowed: set[str], default: str) -> str:
    text = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    return text if text in allowed else default


def _has_storefront_evidence(payload: dict[str, Any], text_blob: str) -> bool:
    facts = payload.get("structured_facts") or []
    has_local = any(
        fact.get("source") == "jsonld_localbusiness"
        or str(fact.get("schema_type") or "").lower() in LOCAL_BUSINESS_TYPES
        for fact in facts
    )
    has_org = any(fact.get("source") == "jsonld_organization" for fact in facts)
    roles = payload.get("enrichment_roles") or []
    has_store_page = "location" in roles
    has_visit = bool(PHYSICAL_YES_RE.search(text_blob))
    has_pin = any(PINCODE_RE.search(item) for item in (payload.get("addresses") or []))
    if has_org and not has_local and not (has_store_page or has_visit or has_pin):
        return False
    return bool(has_local or has_store_page or has_visit or has_pin)


def _allowed_cities(payload: dict[str, Any], text_blob: str) -> list[str]:
    found: list[str] = []
    for city in payload.get("city_mentions") or []:
        found.append(str(city))
    blob = " ".join(
        [
            text_blob,
            " ".join(payload.get("addresses") or []),
            " ".join(
                str(fact.get("value") or "")
                for fact in (payload.get("structured_facts") or [])
                if fact.get("field") in {"city", "addressLocality", "address"}
            ),
        ]
    )
    for city in CITY_NAMES:
        if re.search(rf"\b{re.escape(city)}\b", blob, re.IGNORECASE):
            found.append(city)
    return list(dict.fromkeys(found))


def _strip_think(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    return cleaned.strip()
