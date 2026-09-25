"""Offline tests for optional local Qwen classification."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import requests

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

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
from content_extraction import BusinessSignals, PageEvidence, StructuredFact, extract_page_evidence
from local_llm import (
    LLMCallResult,
    LocalLLMClient,
    ambiguity_reason,
    apply_unknown_fills,
    attach_llm_result,
    build_llm_payload,
    is_rules_confident,
    maybe_classify_with_local_llm,
    validate_llm_result,
)


def _candidate(**kwargs) -> Candidate:
    defaults = dict(
        title="Rozina",
        url="https://rozina.example/",
        normalized_url="https://rozina.example",
        domain="rozina.example",
        snippet="",
        result_type=ResultType.WEBSITE,
        search_query="women's fashion boutique Mumbai",
        search_source="test",
    )
    defaults.update(kwargs)
    return Candidate(**defaults)


def _row(**kwargs) -> BusinessCandidate:
    defaults = dict(
        business_name="Rozina",
        website="https://rozina.example",
        instagram=None,
        facebook=None,
        whatsapp=None,
        phone=None,
        email=None,
        address=None,
        city=UNKNOWN,
        source_url="https://rozina.example",
        source_type="WEBSITE",
        business_type=BusinessType.UNKNOWN,
        fashion_relevance=Relevance.UNKNOWN,
        women_fashion_relevance=Relevance.UNKNOWN,
        physical_store=PhysicalStore.UNKNOWN,
        evidence={"signals": ["direct_website"], "name_source": "homepage_title", "name_confidence": "MEDIUM"},
        confidence=Confidence.MEDIUM,
    )
    defaults.update(kwargs)
    return BusinessCandidate(**defaults)


def _page(**kwargs) -> PageEvidence:
    defaults = dict(
        source_url="https://rozina.example",
        final_url="https://rozina.example",
        domain="rozina.example",
        title="Rozina",
        meta_description="Women's fashion boutique",
        text="Women's clothing boutique in Mumbai. Visit us at our store.",
        headings=["Rozina"],
        links=[],
    )
    defaults.update(kwargs)
    return PageEvidence(**defaults)


def _ai_payload(**overrides) -> dict:
    data = {
        "is_business": True,
        "business_name": "Rozina",
        "business_type": "BOUTIQUE",
        "women_fashion": "HIGH",
        "physical_store": "YES",
        "city": "Mumbai",
        "carries_other_brands": "UNKNOWN",
        "designer_positioning": "INDEPENDENT_BOUTIQUE",
        "sustainability_focus": "UNKNOWN",
        "price_positioning": "UNKNOWN",
        "observed_price_range": "UNKNOWN",
        "style_fit": "UNKNOWN",
        "potential_stockist": "UNKNOWN",
        "confidence": "MEDIUM",
        "evidence": ["boutique language"],
    }
    data.update(overrides)
    return data


class AmbiguityTests(unittest.TestCase):
    def test_directory_is_skipped(self) -> None:
        row = _row(source_type="DIRECTORY")
        reason = ambiguity_reason(row, _candidate(result_type=ResultType.DIRECTORY), _page())
        self.assertEqual(reason, "source_not_website")

    def test_confident_rules_are_skipped(self) -> None:
        row = _row(
            business_type=BusinessType.BOUTIQUE,
            women_fashion_relevance=Relevance.HIGH,
            physical_store=PhysicalStore.YES,
            city="Mumbai",
            confidence=Confidence.HIGH,
            evidence={"name_confidence": "HIGH", "signals": ["direct_website"]},
        )
        self.assertTrue(is_rules_confident(row))
        self.assertEqual(
            ambiguity_reason(row, _candidate(), _page()),
            "rules_already_confident",
        )

    def test_unknown_type_is_ambiguous(self) -> None:
        self.assertIsNone(ambiguity_reason(_row(), _candidate(), _page()))


class PayloadTests(unittest.TestCase):
    def test_payload_is_compact_and_has_no_html(self) -> None:
        page = extract_page_evidence(
            "<html><head><title>Rozina</title></head><body><p>Boutique</p></body></html>",
            source_url="https://rozina.example",
            final_url="https://rozina.example",
        )
        payload = build_llm_payload(
            candidate=_candidate(),
            row=_row(),
            page_evidence=page,
            signals=BusinessSignals(emails=["hello@rozina.example"]),
        )
        self.assertNotIn("html", payload)
        self.assertIn("rule_result", payload)
        self.assertEqual(payload["emails"], ["hello@rozina.example"])
        self.assertLessEqual(len(payload["text_excerpt"]), 1500)


class ValidationTests(unittest.TestCase):
    def test_hq_organization_address_is_not_physical_store(self) -> None:
        payload = build_llm_payload(
            candidate=_candidate(),
            row=_row(),
            page_evidence=_page(
                text="Our company headquarters. Email press@brand.example",
                structured_facts=[
                    StructuredFact(
                        field="name",
                        value="Rozina",
                        source="jsonld_organization",
                        evidence="Rozina",
                        confidence="HIGH",
                        schema_type="Organization",
                    ),
                    StructuredFact(
                        field="address",
                        value="1 HQ Road, Mumbai",
                        source="jsonld_organization",
                        evidence="HQ",
                        confidence="HIGH",
                        schema_type="Organization",
                    ),
                ],
            ),
            signals=BusinessSignals(address_candidates=["1 HQ Road, Mumbai"]),
        )
        validated = validate_llm_result(_ai_payload(physical_store="YES"), payload, _row())
        self.assertEqual(validated["physical_store"], "UNKNOWN")
        self.assertIn("physical_yes_without_storefront", validated["rejected"])

    def test_visit_site_name_rejected(self) -> None:
        payload = build_llm_payload(
            candidate=_candidate(),
            row=_row(),
            page_evidence=_page(),
            signals=BusinessSignals(),
        )
        validated = validate_llm_result(_ai_payload(business_name="Visit Site"), payload, _row())
        self.assertEqual(validated["business_name"], UNKNOWN)

    def test_city_mismatch_with_rules_rejected(self) -> None:
        payload = build_llm_payload(
            candidate=_candidate(),
            row=_row(city="Mumbai"),
            page_evidence=_page(text="Stores in Mumbai and Delhi."),
            signals=BusinessSignals(city_mentions=["Mumbai", "Delhi"]),
        )
        validated = validate_llm_result(_ai_payload(city="Delhi"), payload, _row(city="Mumbai"))
        self.assertEqual(validated["city"], UNKNOWN)
        self.assertIn("city_mismatch_with_rules", validated["rejected"])

    def test_invented_price_rejected(self) -> None:
        payload = build_llm_payload(
            candidate=_candidate(),
            row=_row(),
            page_evidence=_page(text="Women's clothing boutique. Visit us."),
            signals=BusinessSignals(),
        )
        validated = validate_llm_result(
            _ai_payload(price_positioning="LUXURY", observed_price_range="20000-80000"),
            payload,
            _row(),
        )
        self.assertEqual(validated["price_positioning"], "UNKNOWN")
        self.assertEqual(validated["observed_price_range"], "UNKNOWN")
        self.assertIn("price_without_evidence", validated["rejected"])

    def test_sustainability_without_evidence_rejected(self) -> None:
        payload = build_llm_payload(
            candidate=_candidate(),
            row=_row(),
            page_evidence=_page(text="Women's clothing boutique. Visit us."),
            signals=BusinessSignals(),
        )
        validated = validate_llm_result(
            _ai_payload(sustainability_focus="HIGH"),
            payload,
            _row(),
        )
        self.assertEqual(validated["sustainability_focus"], "UNKNOWN")

    def test_roundup_cannot_be_the_business(self) -> None:
        payload = build_llm_payload(
            candidate=_candidate(),
            row=_row(evidence={"signals": ["roundup_page", "publisher_not_boutique"]}),
            page_evidence=_page(),
            signals=BusinessSignals(),
        )
        validated = validate_llm_result(_ai_payload(is_business=True), payload, _row())
        self.assertFalse(validated["is_business"])

    def test_online_only_rule_cannot_become_yes(self) -> None:
        payload = build_llm_payload(
            candidate=_candidate(),
            row=_row(physical_store=PhysicalStore.NO),
            page_evidence=_page(text="Online-only boutique. Women's clothing."),
            signals=BusinessSignals(),
        )
        validated = validate_llm_result(
            _ai_payload(physical_store="YES"),
            payload,
            _row(physical_store=PhysicalStore.NO),
        )
        self.assertEqual(validated["physical_store"], "NO")


class ApplyAndAttachTests(unittest.TestCase):
    def test_attach_does_not_change_rule_fields(self) -> None:
        row = _row(business_type=BusinessType.UNKNOWN)
        attached = attach_llm_result(
            row,
            LLMCallResult(
                enabled=True,
                attempted=True,
                skipped_reason=None,
                error=None,
                raw=_ai_payload(),
                validated=validate_llm_result(
                    _ai_payload(),
                    build_llm_payload(
                        candidate=_candidate(),
                        row=row,
                        page_evidence=_page(),
                        signals=BusinessSignals(),
                    ),
                    row,
                ),
                elapsed_seconds=0.2,
            ),
        )
        self.assertEqual(attached.business_type, BusinessType.UNKNOWN)
        self.assertEqual(attached.evidence["rule_business_type"], "UNKNOWN")
        self.assertEqual(attached.evidence["ai_business_type"], "BOUTIQUE")

    def test_unknown_fills_do_not_override_known_type(self) -> None:
        row = attach_llm_result(
            _row(business_type=BusinessType.BOUTIQUE, city=UNKNOWN),
            LLMCallResult(
                enabled=True,
                attempted=True,
                skipped_reason=None,
                error=None,
                raw=_ai_payload(business_type="DESIGNER", city="Mumbai"),
                validated={
                    **validate_llm_result(
                        _ai_payload(business_type="DESIGNER", city="Mumbai"),
                        build_llm_payload(
                            candidate=_candidate(),
                            row=_row(city=UNKNOWN),
                            page_evidence=_page(text="Women's boutique Mumbai 400050. Visit us."),
                            signals=BusinessSignals(city_mentions=["Mumbai"]),
                        ),
                        _row(city=UNKNOWN),
                    )
                },
                elapsed_seconds=0.1,
            ),
        )
        applied = apply_unknown_fills(row)
        self.assertEqual(applied.business_type, BusinessType.BOUTIQUE)
        self.assertEqual(applied.city, "Mumbai")

    def test_ai_only_types_do_not_alias_into_rule_enum(self) -> None:
        for ai_type in (
            "MULTI_BRAND",
            "CONCEPT_STORE",
            "OWN_BRAND",
            "FASHION_RETAILER",
            "DEPARTMENT_STORE",
            "WHOLESALE",
            "OTHER",
        ):
            with self.subTest(ai_type=ai_type):
                raw = _ai_payload(business_type=ai_type, designer_positioning="DESIGNER_FOCUSED")
                row = attach_llm_result(
                    _row(business_type=BusinessType.UNKNOWN),
                    LLMCallResult(
                        enabled=True,
                        attempted=True,
                        skipped_reason=None,
                        error=None,
                        raw=raw,
                        validated=validate_llm_result(
                            raw,
                            build_llm_payload(
                                candidate=_candidate(),
                                row=_row(),
                                page_evidence=_page(),
                                signals=BusinessSignals(),
                            ),
                            _row(),
                        ),
                        elapsed_seconds=0.1,
                    ),
                )
                applied = apply_unknown_fills(row)
                self.assertEqual(row.evidence["ai_business_type"], ai_type)
                self.assertEqual(
                    row.evidence["ai"]["validated"]["designer_positioning"],
                    "DESIGNER_FOCUSED",
                )
                self.assertEqual(applied.business_type, BusinessType.UNKNOWN)

    def test_exact_rule_overlap_can_fill_unknown_type(self) -> None:
        raw = _ai_payload(business_type="MULTI_DESIGNER")
        row = attach_llm_result(
            _row(business_type=BusinessType.UNKNOWN),
            LLMCallResult(
                enabled=True,
                attempted=True,
                skipped_reason=None,
                error=None,
                raw=raw,
                validated=validate_llm_result(
                    raw,
                    build_llm_payload(
                        candidate=_candidate(),
                        row=_row(),
                        page_evidence=_page(),
                        signals=BusinessSignals(),
                    ),
                    _row(),
                ),
                elapsed_seconds=0.1,
            ),
        )
        applied = apply_unknown_fills(row)
        self.assertEqual(row.evidence["ai_business_type"], "MULTI_DESIGNER")
        self.assertEqual(applied.business_type, BusinessType.MULTI_DESIGNER)


class ClientTests(unittest.TestCase):
    def test_disabled_client_never_calls_ollama(self) -> None:
        session = MagicMock()
        client = LocalLLMClient(enabled=False, session=session)
        result = client.classify(
            candidate=_candidate(),
            row=_row(),
            page_evidence=_page(),
            signals=BusinessSignals(),
        )
        self.assertFalse(result.attempted)
        self.assertEqual(result.skipped_reason, "disabled")
        session.post.assert_not_called()

    def test_timeout_does_not_raise(self) -> None:
        session = MagicMock()
        session.post.side_effect = requests.Timeout()
        client = LocalLLMClient(enabled=True, session=session, timeout=1)
        attached, result = maybe_classify_with_local_llm(
            client,
            candidate=_candidate(),
            row=_row(),
            page_evidence=_page(),
            signals=BusinessSignals(),
        )
        self.assertTrue(result.attempted)
        self.assertIn("Timed out", result.error or "")
        self.assertEqual(attached.business_type, BusinessType.UNKNOWN)
        self.assertIn("ai", attached.evidence)

    def test_successful_json_is_validated(self) -> None:
        session = MagicMock()
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "message": {"content": json.dumps(_ai_payload(physical_store="UNKNOWN"))}
        }
        session.post.return_value = response
        client = LocalLLMClient(enabled=True, session=session)
        result = client.classify(
            candidate=_candidate(),
            row=_row(),
            page_evidence=_page(),
            signals=BusinessSignals(),
        )
        self.assertTrue(result.attempted)
        self.assertIsNone(result.error)
        self.assertEqual(result.validated["business_type"], "BOUTIQUE")
        session.post.assert_called_once()
        body = session.post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "qwen3.5:9b")
        self.assertEqual(body["format"], "json")


if __name__ == "__main__":
    unittest.main()
