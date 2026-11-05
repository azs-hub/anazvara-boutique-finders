"""Focused tests for current in-memory identity normalization."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from deduplication import (
    normalize_instagram,
    normalize_name_city,
    normalize_phone,
    normalize_website_domain,
)


class DeduplicationNormalizationTests(unittest.TestCase):
    def test_website_domain_matches_main_url_normalization(self) -> None:
        self.assertEqual(
            normalize_website_domain("https://www.Example.com:443/about/?utm_source=x"),
            "example.com",
        )

    def test_invalid_website_port_is_rejected(self) -> None:
        self.assertIsNone(
            normalize_website_domain("https://example.com:bad/about")
        )

    def test_instagram_profile_normalizes_to_username(self) -> None:
        self.assertEqual(
            normalize_instagram("https://www.instagram.com/Rozina/"),
            "rozina",
        )
        self.assertEqual(normalize_instagram("@Rozina"), "rozina")
        self.assertEqual(
            normalize_instagram("https://m.instagram.com/Rozina/"),
            "rozina",
        )

    def test_lookalike_instagram_domain_is_rejected(self) -> None:
        self.assertIsNone(
            normalize_instagram("https://notinstagram.com/fake")
        )

    def test_unknown_name_or_city_does_not_form_identity_key(self) -> None:
        self.assertIsNone(normalize_name_city("Rozina", "UNKNOWN"))
        self.assertIsNone(normalize_name_city("UNKNOWN", "Mumbai"))

    def test_phone_formatting_normalizes_to_same_digits(self) -> None:
        self.assertEqual(
            normalize_phone("+91 98765 43210"),
            normalize_phone("91-98765-43210"),
        )


if __name__ == "__main__":
    unittest.main()
