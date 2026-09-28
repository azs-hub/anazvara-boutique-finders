"""Offline tests for stockist-lead export and consistency gating."""

from __future__ import annotations

import csv
import hashlib
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from export_leads import main
from lead_export import (
    assign_export_group,
    business_from_dict,
    export_leads,
    merge_business_records,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row(**kwargs) -> dict:
    evidence = {
        "signals": ["direct_website"],
        "stockist_lead": "UNKNOWN",
        "manual_review": False,
        "stockist_lead_reasons": [],
        "entity_is_business": "YES",
        "entity_relationship": "SELF",
        "geographic_relevance": "YES",
        "geographic_evidence": ["goa_city"],
        "business_context": "INDEPENDENT_RETAIL",
        "entity_quality": "HIGH",
        "ai_potential_stockist": "UNKNOWN",
        "ai": {
            "enabled": True,
            "attempted": False,
            "skipped_reason": None,
            "validated": {
                "is_business": None,
                "potential_stockist": "UNKNOWN",
                "carries_other_brands": "UNKNOWN",
                "designer_positioning": "UNKNOWN",
                "evidence": [],
                "rejected": [],
            },
        },
        "discovered_by_queries": ["women's fashion boutique Testville"],
        "discovery_query": "women's fashion boutique Testville",
    }
    extra_evidence = kwargs.pop("evidence", {})
    evidence.update(extra_evidence)
    payload = {
        "business_name": "Example Collective",
        "website": "https://example-collective.test",
        "instagram": "https://instagram.com/examplecollective",
        "facebook": None,
        "whatsapp": None,
        "phone": None,
        "email": "hello@example-collective.test",
        "address": None,
        "city": "Goa",
        "source_url": "https://example-collective.test",
        "source_type": "WEBSITE",
        "business_type": "BOUTIQUE",
        "fashion_relevance": "HIGH",
        "women_fashion_relevance": "HIGH",
        "physical_store": "UNKNOWN",
        "confidence": "MEDIUM",
        "extra_phones": [],
        "extra_emails": [],
        "evidence": evidence,
    }
    payload.update(kwargs)
    payload["evidence"] = evidence
    return payload


def _benchmark(*rows: dict) -> dict:
    return {
        "query": "multi_query_batch",
        "business_candidates_llm": list(rows),
        "business_candidates_rules": list(rows),
        "organic": {"yes_leads": [rows[0]] if rows else []},
        "candidates": [{"title": "search hit", "url": "https://search.example"}],
    }


def _export(rows: list[dict], tmp: str) -> dict:
    source = Path(tmp) / "benchmark.json"
    source.write_text(json.dumps(_benchmark(*rows)), encoding="utf-8")
    return export_leads(source, output_dir=tmp)


class LeadExportOfflineTests(unittest.TestCase):
    def test_export_does_not_open_network_or_call_qwen(self) -> None:
        source_text = Path(SRC_DIR / "lead_export.py").read_text(encoding="utf-8")
        self.assertNotIn("local_llm", source_text)
        self.assertNotIn("ollama", source_text.lower())
        self.assertNotIn("searxng", source_text.lower())
        self.assertNotIn("PageFetcher", source_text)

        def _blocked(*_args, **_kwargs):
            raise AssertionError("network access is not allowed")

        with tempfile.TemporaryDirectory() as tmp:
            with patch("socket.socket", side_effect=_blocked), patch(
                "socket.create_connection", side_effect=_blocked
            ):
                result = _export(
                    [
                        _row(
                            evidence={
                                "stockist_lead": "YES",
                                "ai_potential_stockist": "YES",
                                "stockist_lead_reasons": ["fashion_retail_page_evidence"],
                                "ai": {
                                    "attempted": True,
                                    "skipped_reason": None,
                                    "validated": {
                                        "is_business": True,
                                        "potential_stockist": "YES",
                                        "carries_other_brands": "YES",
                                        "evidence": ["A concept store carrying independent labels."],
                                        "rejected": [],
                                    },
                                },
                            }
                        )
                    ],
                    tmp,
                )
        self.assertEqual(result["summary"]["lead_count"], 1)
        self.assertNotIn("local_llm", sys.modules.get("lead_export").__dict__)

    def test_original_benchmark_remains_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "benchmark.json"
            source.write_text(json.dumps(_benchmark(_row())), encoding="utf-8")
            before = _sha256(source)
            export_leads(source, output_dir=tmp)
            self.assertEqual(_sha256(source), before)

    def test_csv_and_json_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _export(
                [
                    _row(
                        evidence={
                            "stockist_lead": "YES",
                            "ai_potential_stockist": "YES",
                            "stockist_lead_reasons": ["fashion_retail_page_evidence"],
                            "ai": {
                                "attempted": True,
                                "validated": {
                                    "is_business": True,
                                    "potential_stockist": "YES",
                                    "carries_other_brands": "YES",
                                    "evidence": ["Concept store with curated fashion."],
                                    "rejected": [],
                                },
                            },
                        }
                    )
                ],
                tmp,
            )
            for name in (
                "stockist_leads.csv",
                "stockist_leads.json",
                "stockist_export_summary.json",
                "stockist_leads_only.csv",
                "stockist_review_only.csv",
                "stockist_excluded.csv",
            ):
                self.assertTrue((Path(tmp) / name).is_file(), name)
            with (Path(tmp) / "stockist_leads.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["business_name"], "Example Collective")
            self.assertEqual(rows[0]["export_group"], "LEAD")
            payload = json.loads((Path(tmp) / "stockist_leads.json").read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["export_group"], "LEAD")
            self.assertEqual(result["summary"]["unique_business_candidates"], 1)

    def test_one_row_per_verified_business_and_provenance(self) -> None:
        first = _row(
            source_url="https://magazine.example/guide",
            evidence={
                "stockist_lead": "YES",
                "ai_potential_stockist": "YES",
                "discovered_by_queries": ["query one"],
                "discovery_query": "query one",
                "stockist_lead_reasons": ["fashion_retail_page_evidence"],
                "ai": {
                    "attempted": True,
                    "validated": {
                        "is_business": True,
                        "potential_stockist": "YES",
                        "carries_other_brands": "YES",
                        "evidence": ["Official concept store."],
                        "rejected": [],
                    },
                },
            },
        )
        second = _row(
            source_url="https://example-collective.test/about",
            instagram="https://instagram.com/examplecollective/",
            evidence={
                "stockist_lead": "YES",
                "ai_potential_stockist": "YES",
                "discovered_by_queries": ["query two"],
                "discovery_query": "query two",
                "stockist_lead_reasons": ["fashion_retail_page_evidence"],
                "ai": {
                    "attempted": True,
                    "validated": {
                        "is_business": True,
                        "potential_stockist": "YES",
                        "carries_other_brands": "YES",
                        "evidence": ["Official concept store."],
                        "rejected": [],
                    },
                },
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = _export([first, second], tmp)
        self.assertEqual(result["summary"]["total_original_records"], 2)
        self.assertEqual(result["summary"]["unique_business_candidates"], 1)
        self.assertEqual(result["summary"]["duplicates_merged"], 1)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(
            set(result["rows"][0]["discovered_by_queries"]),
            {"query one", "query two"},
        )
        self.assertIn("https://magazine.example/guide", result["rows"][0]["source_urls"])
        self.assertIn(
            "https://example-collective.test/about",
            result["rows"][0]["source_urls"],
        )

    def test_similar_names_with_different_sites_are_not_merged(self) -> None:
        left = _row(
            business_name="Shared Name Shop",
            website="https://alpha-shop.test",
            instagram="https://instagram.com/alphashop",
            source_url="https://alpha-shop.test",
            city="Goa",
        )
        right = _row(
            business_name="Shared Name Shop",
            website="https://beta-shop.test",
            instagram="https://instagram.com/betashop",
            source_url="https://beta-shop.test",
            city="Goa",
        )
        merged, duplicates, ambiguous = merge_business_records(
            [business_from_dict(left), business_from_dict(right)]
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(duplicates, 0)
        self.assertTrue(ambiguous)

    def test_article_or_directory_is_excluded(self) -> None:
        article = _row(
            business_name="Boutiques To Visit Nearby",
            website="https://holiday.example/boutiques-to-shop-around-town",
            source_url="https://holiday.example/boutiques-to-shop-around-town",
            instagram=None,
            email=None,
            evidence={
                "stockist_lead": "YES",
                "ai_potential_stockist": "NO",
                "entity_relationship": "SELF",
                "entity_is_business": "YES",
                "entity_quality": "HIGH",
                "ai": {
                    "attempted": True,
                    "validated": {
                        "is_business": False,
                        "potential_stockist": "NO",
                        "carries_other_brands": "UNKNOWN",
                        "evidence": [
                            "This is a curated list / listicle of other shops, not the business itself."
                        ],
                        "rejected": ["directory_or_article_not_business"],
                    },
                },
            },
        )
        directory = _row(
            business_name="Local Listings",
            website=None,
            instagram=None,
            email=None,
            source_type="DIRECTORY",
            source_url="https://listings.example/goa",
            evidence={
                "stockist_lead": "YES",
                "entity_relationship": "DIRECTORY",
                "entity_is_business": "NO",
                "signals": ["directory"],
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = _export([article, directory], tmp)
        groups = {row["business_name"]: row["export_group"] for row in result["rows"]}
        self.assertEqual(groups["Boutiques To Visit Nearby"], "EXCLUDED")
        self.assertEqual(groups["Local Listings"], "EXCLUDED")
        self.assertGreaterEqual(result["summary"]["contradictory_classifications_detected"], 1)

    def test_contradictory_qwen_and_lead_is_not_an_approved_lead(self) -> None:
        row = business_from_dict(
            _row(
                evidence={
                    "stockist_lead": "YES",
                    "ai_potential_stockist": "NO",
                    "entity_relationship": "SELF",
                    "ai": {
                        "attempted": True,
                        "validated": {
                            "is_business": False,
                            "potential_stockist": "NO",
                            "evidence": [
                                "The page is an article and listicle rather than a retailer."
                            ],
                            "rejected": ["directory_or_article_not_business"],
                        },
                    },
                }
            )
        )
        decision = assign_export_group(row)
        self.assertEqual(decision["export_group"], "EXCLUDED")
        self.assertTrue(decision["conflicts"])

    def test_facebook_title_with_boutique_is_not_enough_for_lead(self) -> None:
        row = _row(
            business_name="Sample Boutique Goa",
            website=None,
            email=None,
            phone=None,
            instagram=None,
            facebook="https://facebook.com/SampleBoutiqueGoa",
            source_type="SOCIAL",
            source_url="https://facebook.com/SampleBoutiqueGoa",
            evidence={
                "signals": ["social_profile"],
                "stockist_lead": "UNKNOWN",
                "stockist_lead_reasons": ["official_identity_weak"],
                "entity_quality": "WEAK",
                "ai": {
                    "attempted": False,
                    "skipped_reason": "source_not_website",
                    "validated": {
                        "is_business": None,
                        "potential_stockist": "UNKNOWN",
                        "evidence": [],
                        "rejected": [],
                    },
                },
                "qwen_inspection": {
                    "payload": {
                        "page_title": "Sample Boutique Goa | Calangute - Facebook",
                        "text_excerpt": "",
                    }
                },
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = _export([row], tmp)
        self.assertEqual(result["rows"][0]["export_group"], "REVIEW")

    def test_social_only_insufficient_evidence_goes_to_review(self) -> None:
        row = _row(
            website=None,
            email=None,
            phone=None,
            facebook=None,
            instagram="https://instagram.com/sample.boutique.profile",
            source_type="SOCIAL",
            source_url="https://instagram.com/sample.boutique.profile",
            evidence={
                "signals": ["social_profile"],
                "stockist_lead": "YES",
                "ai_potential_stockist": "UNKNOWN",
                "stockist_lead_reasons": ["contactable"],
                "entity_relationship": "SELF",
                "entity_is_business": "YES",
                "geographic_relevance": "YES",
                "ai": {
                    "attempted": False,
                    "skipped_reason": "source_not_website",
                    "validated": {
                        "is_business": None,
                        "potential_stockist": "UNKNOWN",
                        "evidence": [],
                        "rejected": [],
                    },
                },
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = _export([row], tmp)
        self.assertEqual(result["rows"][0]["export_group"], "REVIEW")
        self.assertIn("social_identity", result["rows"][0]["export_reason"])

    def test_qwen_skipped_on_instagram_is_not_treated_as_invalid(self) -> None:
        row = business_from_dict(
            _row(
                website=None,
                email=None,
                instagram="https://instagram.com/official.profile",
                source_type="SOCIAL",
                source_url="https://instagram.com/official.profile",
                evidence={
                    "signals": ["social_profile"],
                    "stockist_lead": "UNKNOWN",
                    "ai": {
                        "attempted": False,
                        "skipped_reason": "source_not_website",
                        "validated": {
                            "is_business": None,
                            "potential_stockist": "UNKNOWN",
                            "evidence": [],
                            "rejected": [],
                        },
                    },
                },
            )
        )
        decision = assign_export_group(row)
        self.assertNotEqual(decision["export_group"], "EXCLUDED")
        self.assertFalse(
            any("invalid" in reason for reason in decision["export_reasons"])
        )

    def test_wrong_geography_is_excluded(self) -> None:
        row = _row(
            city="Waterford",
            website="https://foreign-boutique.ie",
            source_url="https://foreign-boutique.ie",
            evidence={
                "stockist_lead": "YES",
                "geographic_relevance": "NO",
                "geographic_evidence": ["foreign_website_tld"],
                "entity_quality": "REJECTED",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = _export([row], tmp)
        self.assertEqual(result["rows"][0]["export_group"], "EXCLUDED")
        self.assertEqual(result["rows"][0]["export_reason"], "wrong_geography")

    def test_missing_contact_path_is_review_not_excluded(self) -> None:
        row = _row(
            website=None,
            instagram=None,
            facebook=None,
            email=None,
            phone=None,
            evidence={
                "stockist_lead": "UNKNOWN",
                "stockist_lead_reasons": [
                    "fashion_retail_page_evidence",
                    "no_usable_contact_path",
                ],
                "ai_potential_stockist": "UNKNOWN",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = _export([row], tmp)
        self.assertEqual(result["rows"][0]["export_group"], "REVIEW")
        self.assertEqual(result["rows"][0]["export_reason"], "no_usable_contact_path")

    def test_own_label_with_evidence_is_excluded(self) -> None:
        row = _row(
            business_type="DESIGNER",
            evidence={
                "stockist_lead": "YES",
                "ai_potential_stockist": "NO",
                "ai": {
                    "attempted": True,
                    "validated": {
                        "is_business": True,
                        "potential_stockist": "NO",
                        "carries_other_brands": "NO",
                        "designer_positioning": "OWN_LABEL",
                        "evidence": ["Single-brand store selling only its own label."],
                        "rejected": ["own_label_not_stockist"],
                    },
                },
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = _export([row], tmp)
        self.assertEqual(result["rows"][0]["export_group"], "EXCLUDED")
        self.assertIn("own_label", result["rows"][0]["export_reason"])

    def test_negated_multi_brand_language_is_review_not_lead(self) -> None:
        row = _row(
            evidence={
                "stockist_lead": "YES",
                "ai_potential_stockist": "UNKNOWN",
                "stockist_lead_reasons": ["fashion_retail_page_evidence"],
                "ai": {
                    "attempted": True,
                    "validated": {
                        "is_business": True,
                        "potential_stockist": "UNKNOWN",
                        "carries_other_brands": "UNKNOWN",
                        "evidence": [
                            "No explicit mention of carrying other brands or being a multi-brand concept store is found.",
                            "The text excerpt is empty so we cannot confirm if it carries multiple independent labels.",
                        ],
                        "rejected": [],
                    },
                },
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = _export([row], tmp)
        self.assertEqual(result["rows"][0]["export_group"], "REVIEW")
        self.assertEqual(result["rows"][0]["export_reason"], "multi_brand_status_unknown")

    def test_stockist_lead_yes_alone_is_not_enough_for_lead(self) -> None:
        row = _row(
            website="https://news.example/release/store-opening",
            source_url="https://news.example/release/store-opening",
            instagram=None,
            email=None,
            evidence={
                "stockist_lead": "YES",
                "ai_potential_stockist": "NO",
                "entity_relationship": "SELF",
                "entity_is_business": "YES",
                "ai": {
                    "attempted": True,
                    "validated": {
                        "is_business": False,
                        "potential_stockist": "NO",
                        "evidence": ["The page is a press release / news article, not the retailer."],
                        "rejected": ["directory_or_article_not_business"],
                    },
                },
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = _export([row], tmp)
        self.assertNotEqual(result["rows"][0]["export_group"], "LEAD")

    def test_cli_writes_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "benchmark.json"
            source.write_text(json.dumps(_benchmark(_row())), encoding="utf-8")
            code = main([str(source), "--output-dir", tmp])
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "stockist_leads.csv").is_file())

    def test_production_export_does_not_hardcode_businesses(self) -> None:
        for name in ("lead_export.py", "export_leads.py"):
            source = (SRC_DIR / name).read_text(encoding="utf-8").lower()
            self.assertNotIn("rangeela", source)
            self.assertNotIn("rainforest", source)
            self.assertNotIn("maya boutique", source)
            self.assertNotIn("whats hot", source)


class NoNetworkSocketGuard(unittest.TestCase):
    def test_socket_is_not_needed_for_export(self) -> None:
        original = socket.socket

        class ClosedSocket(socket.socket):
            def __init__(self, *args, **kwargs):
                raise AssertionError("socket opened")

        socket.socket = ClosedSocket  # type: ignore[method-assign]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                _export([_row()], tmp)
        finally:
            socket.socket = original  # type: ignore[method-assign]


if __name__ == "__main__":
    unittest.main()
