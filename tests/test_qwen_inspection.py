"""Offline tests for Qwen inspection helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

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
from qwen_inspection import (
    build_qwen_inspection,
    diagnose_qwen,
    evidence_sufficient_label,
    stockist_evidence_available,
)


def _row(**kwargs) -> BusinessCandidate:
    defaults = dict(
        business_name="Example Store",
        website="https://example.com",
        instagram=None,
        facebook=None,
        whatsapp=None,
        phone=None,
        email=None,
        address=None,
        city="Goa",
        source_url="https://example.com",
        source_type="WEBSITE",
        business_type=BusinessType.UNKNOWN,
        fashion_relevance=Relevance.UNKNOWN,
        women_fashion_relevance=Relevance.UNKNOWN,
        physical_store=PhysicalStore.UNKNOWN,
        evidence={},
        confidence=Confidence.MEDIUM,
    )
    defaults.update(kwargs)
    return BusinessCandidate(**defaults)


class QwenInspectionTests(unittest.TestCase):
    def test_concept_store_excerpt_is_sufficient(self) -> None:
        payload = {
            "text_excerpt": "A concept store with luxury clothing and curated collections.",
            "page_title": "Concept store",
            "headings": [],
        }
        self.assertTrue(stockist_evidence_available(payload))
        self.assertEqual(evidence_sufficient_label(payload), "YES")

    def test_thin_excerpt_is_not_sufficient(self) -> None:
        payload = {"text_excerpt": "Hello", "page_title": "", "headings": []}
        self.assertFalse(stockist_evidence_available(payload))
        self.assertEqual(evidence_sufficient_label(payload), "NO")

    def test_diagnosis_a_when_qwen_says_no_despite_concept_store(self) -> None:
        row = _row(
            evidence={
                "ai_potential_stockist": "NO",
                "ai": {"validated": {"carries_other_brands": "UNKNOWN"}},
            }
        )
        inspection = {
            "attempted": True,
            "payload": {
                "text_excerpt": "A concept store in Assagao. Luxury clothing collections.",
                "page_title": "Concept store",
                "headings": ["About"],
            },
        }
        self.assertEqual(diagnose_qwen(row, inspection), "A")

    def test_diagnosis_d_for_own_label(self) -> None:
        row = _row(
            evidence={
                "ai_potential_stockist": "NO",
                "ai": {"validated": {"designer_positioning": "OWN_LABEL"}},
            }
        )
        inspection = {
            "attempted": True,
            "payload": {"text_excerpt": "Designer studio. Our own collection.", "headings": []},
        }
        self.assertEqual(diagnose_qwen(row, inspection), "D")

    def test_only_valid_goa_self_rows_are_listed(self) -> None:
        keep = _row(
            evidence={
                "entity_relationship": "SELF",
                "entity_is_business": "YES",
                "geographic_relevance": "YES",
                "ai_potential_stockist": "YES",
                "qwen_inspection": {"attempted": True, "payload": {"text_excerpt": "concept store"}},
            }
        )
        drop = _row(
            business_name="Article",
            evidence={
                "entity_relationship": "ARTICLE",
                "entity_is_business": "NO",
                "geographic_relevance": "YES",
                "ai_potential_stockist": "NO",
            },
        )
        report = build_qwen_inspection([keep, drop])
        self.assertEqual(report["valid_goa_self_businesses"], 1)
        self.assertEqual(report["businesses"][0]["business_name"], "Example Store")

    def test_module_does_not_hardcode_names(self) -> None:
        source = (SRC_DIR / "qwen_inspection.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("rangeela", source)
        self.assertNotIn("yellow house", source)


if __name__ == "__main__":
    unittest.main()
