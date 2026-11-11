"""Isolated boutique deduplication helpers.

Identifier priority (first match wins when the value is present):

1. Normalized website domain
2. Normalized Instagram username
3. Normalized phone number
4. Normalized boutique name + city

Keep matching rules here so they can be tightened later without touching
SQLite schema code or Excel export.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from scraper import BoutiqueRecord
from url_normalization import extract_domain


def normalize_website_domain(website: str | None) -> str | None:
    """Return a lowercase hostname without a leading ``www.`` prefix."""
    if not website or not str(website).strip():
        return None
    return extract_domain(str(website).strip())


def normalize_instagram(instagram: str | None) -> str | None:
    """Return a lowercase Instagram username without ``@`` or URL path."""
    if not instagram or not str(instagram).strip():
        return None
    value = str(instagram).strip()
    lower = value.lower()
    if "://" in value or lower.startswith(("instagram.com/", "www.instagram.com/")):
        parsed_value = value if "://" in value else f"https://{value}"
        domain = extract_domain(parsed_value)
        if not domain or not (
            domain == "instagram.com" or domain.endswith(".instagram.com")
        ):
            return None
        path = urlparse(parsed_value).path
        value = path.strip("/")
    value = value.lstrip("@").strip().lower()
    value = value.split("/")[0]
    return value or None


def normalize_phone(phone: str | None) -> str | None:
    """Return a digits-only phone identity key."""
    if not phone or not str(phone).strip():
        return None
    raw = str(phone).strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    return digits


def normalize_name_city(name: str | None, city: str | None) -> str | None:
    """Return a compact ``name|city`` key used as the weakest identifier."""
    if not name or not city:
        return None
    compact_name = re.sub(r"\s+", " ", str(name).strip().lower())
    compact_city = re.sub(r"\s+", " ", str(city).strip().lower())
    if (
        not compact_name
        or not compact_city
        or compact_name == "unknown"
        or compact_city == "unknown"
    ):
        return None
    return f"{compact_name}|{compact_city}"


def identity_keys(record: BoutiqueRecord | dict) -> dict[str, str | None]:
    """Compute normalized identity keys for a boutique record."""
    if isinstance(record, BoutiqueRecord):
        data = record.to_dict()
    else:
        data = record
    return {
        "website_domain": normalize_website_domain(data.get("website")),
        "instagram_username": normalize_instagram(data.get("instagram")),
        "phone_normalized": normalize_phone(data.get("phone")),
        "name_city_key": normalize_name_city(data.get("name"), data.get("city")),
    }


def is_same_boutique(
    candidate: BoutiqueRecord | dict,
    existing: BoutiqueRecord | dict,
) -> bool:
    """Return True if ``candidate`` matches ``existing`` on any identity key.

    Comparison follows the documented priority order and ignores empty keys.
    """
    candidate_keys = identity_keys(candidate)
    existing_keys = identity_keys(existing)
    for field in (
        "website_domain",
        "instagram_username",
        "phone_normalized",
        "name_city_key",
    ):
        left = candidate_keys[field]
        right = existing_keys[field]
        if left and right and left == right:
            return True
    return False
