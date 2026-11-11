"""Extract page evidence and explicit business signals from HTML.

This is not boutique identification and does not assign scores.
Scripts, styles, and obvious cookie/nav chrome are dropped. Footer text
is kept because it often contains contact details.

Limits
------
- ``MAX_TEXT_CHARS``: 50_000
- ``MAX_LINKS``: 200
- ``MAX_HEADINGS``: 40
- ``MAX_SIGNAL_ITEMS``: 30
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from classification import SOCIAL_DOMAINS, VIDEO_DOMAINS, _host_matches
from url_normalization import extract_domain, normalize_url

MAX_TEXT_CHARS = 50_000
MAX_LINKS = 200
MAX_HEADINGS = 40
MAX_SIGNAL_ITEMS = 30
MAX_STRUCTURED_FACTS = 40

LOCAL_BUSINESS_TYPES = {
    "localbusiness",
    "store",
    "clothingstore",
    "fashionstore",
    "shoestore",
    "jewelrystore",
    "apparelstore",
}
ORGANIZATION_TYPES = {"organization", "brand", "corporation", "ngo"}
WEBSITE_TYPES = {"website"}
ADDRESS_TYPES = {"postaladdress"}
CONTACT_TYPES = {"contactpoint"}

USEFUL_PATH_TOKENS = (
    "about",
    "contact",
    "store",
    "location",
    "shop",
    "collection",
    "designer",
    "brand",
    "home",
    "visit",
    "address",
    "boutique",
)

EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
PHONE_RE = re.compile(
    r"(?:\+|00)?(?:91[\s\-()]*)?(?:\d[\s\-()]*){9,14}\d",
)
PINCODE_RE = re.compile(r"\b[1-9]\d{5}\b")
ADDRESS_HINT_RE = re.compile(
    r"\b(road|rd\.?|street|st\.?|marg|nagar|lane|avenue|cross|bazar|market|"
    r"complex|plaza|floor|shop\s*no|unit|bandra|andheri|colaba|fort|"
    r"pincode|pin\s*code)\b",
    re.IGNORECASE,
)
ECOMMERCE_ADDRESS_NOISE_RE = re.compile(
    r"\b(unit\s+price|regular\s+price|quick\s+view|add(?:ed)?\s+to\s+cart)\b"
    r"|(?:₹|\$|\brs\.?)\s*[\d,]+(?:\.\d{2})?",
    re.IGNORECASE,
)
CITY_NAMES = (
    "Mumbai",
    "Delhi",
    "New Delhi",
    "Bengaluru",
    "Bangalore",
    "Hyderabad",
    "Chennai",
    "Kolkata",
    "Pune",
    "Ahmedabad",
    "Jaipur",
    "Surat",
    "Goa",
    "Navi Mumbai",
    "Thane",
    "Bandra",
    "Khar",
    "Colaba",
)

NOISE_TAGS = ("script", "style", "noscript", "svg", "template", "iframe")


@dataclass(frozen=True)
class ExtractedLink:
    """One absolute (when possible) hyperlink from the page."""

    url: str
    normalized_url: str | None
    anchor_text: str
    useful: bool
    external: bool


@dataclass(frozen=True)
class StructuredFact:
    """One extracted field with source provenance. Never inferred."""

    field: str
    value: str
    source: str
    evidence: str
    confidence: str
    schema_type: str | None = None


@dataclass(frozen=True)
class PageEvidence:
    """Raw evidence from one fetched page. Not a boutique record."""

    source_url: str
    final_url: str | None
    domain: str | None
    title: str | None
    meta_description: str | None
    text: str
    headings: list[str]
    links: list[ExtractedLink]
    og_site_name: str | None = None
    meta_brand: str | None = None
    structured_facts: list[StructuredFact] = field(default_factory=list)


@dataclass(frozen=True)
class BusinessSignals:
    """Contact/location strings found on the page. Evidence only."""

    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    social_urls: list[str] = field(default_factory=list)
    whatsapp_urls: list[str] = field(default_factory=list)
    address_candidates: list[str] = field(default_factory=list)
    city_mentions: list[str] = field(default_factory=list)


def extract_page_evidence(
    html: str,
    *,
    source_url: str,
    final_url: str | None = None,
) -> PageEvidence:
    """Parse HTML into title, text, headings, and absolute links."""
    base = final_url or source_url
    soup = BeautifulSoup(html, "html.parser")
    og_site_name, meta_brand, structured_facts = extract_structured_metadata(soup)
    _strip_noise(soup)

    title = _page_title(soup)
    meta_description = _meta_description(soup)
    headings = _headings(soup)
    text = _visible_text(soup)
    mailto_emails, tel_phones = _mailto_and_tel(soup)
    extras = " ".join(mailto_emails + tel_phones)
    if extras:
        text = f"{text} {extras}".strip()
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS]
    links = _extract_links(soup, base)
    return PageEvidence(
        source_url=source_url,
        final_url=final_url,
        domain=extract_domain(final_url or source_url),
        title=title,
        meta_description=meta_description,
        text=text,
        headings=headings,
        links=links,
        og_site_name=og_site_name,
        meta_brand=meta_brand,
        structured_facts=structured_facts,
    )


def extract_business_signals(
    evidence: PageEvidence,
) -> BusinessSignals:
    """Pull emails, phones, social/WhatsApp URLs, and address-like snippets."""
    haystack = " ".join(
        part
        for part in (
            evidence.title,
            evidence.meta_description,
            evidence.text,
            " ".join(evidence.headings),
        )
        if part
    )
    emails = _unique(_emails_from_text(haystack) + _emails_from_links(evidence.links))
    phones = _unique(_phones_from_text(haystack) + _phones_from_links(evidence.links))
    social = _unique(_social_from_links(evidence.links) + _social_from_text(haystack))
    whatsapp = _unique(_whatsapp_from_links(evidence.links) + _whatsapp_from_text(haystack))
    addresses = _unique(_address_candidates(evidence.text))
    cities = _unique(_city_mentions(haystack))
    for fact in evidence.structured_facts:
        if fact.field == "email":
            emails.append(fact.value)
        elif fact.field == "telephone":
            phones.append(fact.value)
        elif fact.field == "sameAs":
            lowered = fact.value.lower()
            if "instagram.com" in lowered or "facebook.com" in lowered or "fb.com" in lowered:
                social.append(fact.value)
            if "wa.me" in lowered or "whatsapp" in lowered:
                whatsapp.append(fact.value)
        elif fact.field == "address":
            addresses.append(fact.value)
        elif fact.field in {"city", "addressLocality"}:
            cities.append(fact.value)
    return BusinessSignals(
        emails=_unique(emails)[:MAX_SIGNAL_ITEMS],
        phones=_unique(phones)[:MAX_SIGNAL_ITEMS],
        social_urls=_unique(social)[:MAX_SIGNAL_ITEMS],
        whatsapp_urls=_unique(whatsapp)[:MAX_SIGNAL_ITEMS],
        address_candidates=_unique(addresses)[:MAX_SIGNAL_ITEMS],
        city_mentions=_unique(cities)[:MAX_SIGNAL_ITEMS],
    )


def empty_evidence(source_url: str, *, title: str | None = None) -> PageEvidence:
    """Minimal evidence when a fetch is skipped or blocked (e.g. SOCIAL)."""
    return PageEvidence(
        source_url=source_url,
        final_url=None,
        domain=extract_domain(source_url),
        title=title,
        meta_description=None,
        text="",
        headings=[],
        links=[],
    )


def empty_signals() -> BusinessSignals:
    return BusinessSignals()


def extract_structured_metadata(
    soup: BeautifulSoup,
) -> tuple[str | None, str | None, list[StructuredFact]]:
    """Read JSON-LD and brand meta before scripts are stripped as noise."""
    og_site_name = _meta_content(soup, property="og:site_name")
    meta_brand = (
        _meta_content(soup, name="application-name")
        or _meta_content(soup, name="apple-mobile-web-app-title")
        or _meta_content(soup, name="publisher")
        or _meta_content(soup, property="og:brand")
    )
    facts = _facts_from_jsonld(soup)
    if og_site_name:
        facts.append(
            StructuredFact(
                field="name",
                value=og_site_name,
                source="og_site_name",
                evidence=og_site_name,
                confidence="HIGH",
            )
        )
    if meta_brand:
        facts.append(
            StructuredFact(
                field="name",
                value=meta_brand,
                source="meta_brand",
                evidence=meta_brand,
                confidence="MEDIUM",
            )
        )
    return og_site_name, meta_brand, facts[:MAX_STRUCTURED_FACTS]


def _meta_content(soup: BeautifulSoup, **attrs: str) -> str | None:
    tag = soup.find("meta", attrs=attrs)
    if isinstance(tag, Tag) and tag.get("content"):
        value = str(tag["content"]).strip()
        return value or None
    return None


def _facts_from_jsonld(soup: BeautifulSoup) -> list[StructuredFact]:
    facts: list[StructuredFact] = []
    for tag in soup.find_all("script"):
        if not isinstance(tag, Tag):
            continue
        script_type = (tag.get("type") or "").split(";")[0].strip().lower()
        if script_type != "application/ld+json":
            continue
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(_strip_jsonld_cdata(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        for node in _flatten_jsonld(payload):
            facts.extend(_facts_from_jsonld_node(node))
            if len(facts) >= MAX_STRUCTURED_FACTS:
                return facts[:MAX_STRUCTURED_FACTS]
    return facts


def _strip_jsonld_cdata(raw: str) -> str:
    text = raw.strip()
    if text.startswith("/*"):
        text = re.sub(r"^/\*.*?\*/", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.DOTALL)
    return text.strip()


def _flatten_jsonld(node) -> list[dict]:
    found: list[dict] = []

    def walk(value) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        if "@graph" in value:
            walk(value["@graph"])
        if value.get("@type") or value.get("name") or value.get("address"):
            found.append(value)
        for key, child in value.items():
            if key in {"@context", "@id", "@type", "@graph"}:
                continue
            if isinstance(child, (dict, list)):
                walk(child)

    walk(node)
    return found


def _jsonld_types(node: dict) -> list[str]:
    raw = node.get("@type")
    if isinstance(raw, list):
        return [str(item).split("/")[-1] for item in raw if item]
    if raw:
        return [str(raw).split("/")[-1]]
    return []


def _jsonld_source(types: list[str]) -> tuple[str, str | None]:
    lowered = [item.lower() for item in types]
    for item, original in zip(lowered, types):
        if item in LOCAL_BUSINESS_TYPES:
            return "jsonld_localbusiness", original
        if item in ADDRESS_TYPES:
            return "jsonld_postaladdress", original
        if item in CONTACT_TYPES:
            return "jsonld_contactpoint", original
        if item in WEBSITE_TYPES:
            return "jsonld_website", original
        if item in ORGANIZATION_TYPES:
            return "jsonld_organization", original
    if types:
        return "jsonld_other", types[0]
    return "jsonld_other", None


def _text_value(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "value", "text", "@value"):
            inner = _text_value(value.get(key))
            if inner:
                return inner
    return None


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            text = _text_value(item)
            if text:
                found.append(text)
        return found
    text = _text_value(value)
    return [text] if text else []


def _format_postal_address(node: dict) -> str | None:
    parts = [
        _text_value(node.get("streetAddress")),
        _text_value(node.get("addressLocality")),
        _text_value(node.get("addressRegion")),
        _text_value(node.get("postalCode")),
        _text_value(node.get("addressCountry")),
    ]
    compact = ", ".join(part for part in parts if part)
    return compact or None


def _facts_from_jsonld_node(node: dict) -> list[StructuredFact]:
    types = _jsonld_types(node)
    source, schema_type = _jsonld_source(types)
    facts: list[StructuredFact] = []
    interesting = source != "jsonld_other" or bool(
        node.get("address") or node.get("telephone") or node.get("email")
    )
    if not interesting:
        return facts

    def add(field: str, value: str | None, *, evidence: str | None = None, confidence: str = "HIGH") -> None:
        if not value:
            return
        facts.append(
            StructuredFact(
                field=field,
                value=value,
                source=source,
                evidence=evidence or value,
                confidence=confidence,
                schema_type=schema_type,
            )
        )

    if source in {
        "jsonld_organization",
        "jsonld_localbusiness",
        "jsonld_website",
    }:
        add("name", _text_value(node.get("name")))
        add("url", _text_value(node.get("url")))
        add("telephone", _text_value(node.get("telephone")))
        add("email", _text_value(node.get("email")))
        for social in _string_list(node.get("sameAs")):
            add("sameAs", social)
        address = node.get("address")
        if isinstance(address, dict):
            formatted = _format_postal_address(address) or _text_value(address)
            add("address", formatted, evidence="PostalAddress")
            add("addressLocality", _text_value(address.get("addressLocality")))
            add("addressRegion", _text_value(address.get("addressRegion")))
            add("city", _text_value(address.get("addressLocality")))
        elif isinstance(address, list):
            for item in address:
                if isinstance(item, dict):
                    formatted = _format_postal_address(item) or _text_value(item)
                    add("address", formatted, evidence="PostalAddress")
                    add("city", _text_value(item.get("addressLocality")))
                    add("addressLocality", _text_value(item.get("addressLocality")))
                else:
                    add("address", _text_value(item))
        else:
            add("address", _text_value(address))
        contact = node.get("contactPoint")
        points = contact if isinstance(contact, list) else [contact] if contact else []
        for point in points:
            if not isinstance(point, dict):
                continue
            add("telephone", _text_value(point.get("telephone")), evidence="ContactPoint")
            add("email", _text_value(point.get("email")), evidence="ContactPoint")
    elif source == "jsonld_postaladdress":
        add("address", _format_postal_address(node) or _text_value(node.get("name")))
        add("addressLocality", _text_value(node.get("addressLocality")))
        add("addressRegion", _text_value(node.get("addressRegion")))
        add("city", _text_value(node.get("addressLocality")))
    elif source == "jsonld_contactpoint":
        add("telephone", _text_value(node.get("telephone")))
        add("email", _text_value(node.get("email")))
    if source == "jsonld_localbusiness":
        add(
            "physical_store",
            "true",
            evidence=schema_type or "LocalBusiness",
            confidence="HIGH",
        )
    return facts


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()
    for tag in soup.find_all("nav"):
        tag.decompose()
    for tag in list(soup.find_all(True)):
        if not isinstance(tag, Tag) or tag.attrs is None:
            continue
        ident = tag.get("id") or ""
        classes = tag.get("class") or []
        if isinstance(classes, str):
            class_blob = classes
        else:
            class_blob = " ".join(str(item) for item in classes)
        blob = f"{ident} {class_blob}".lower()
        if "cookie" in blob or "consent" in blob:
            tag.decompose()


def _page_title(soup: BeautifulSoup) -> str | None:
    og = soup.find("meta", attrs={"property": "og:title"})
    if isinstance(og, Tag) and og.get("content"):
        return str(og["content"]).strip() or None
    if soup.title and soup.title.string:
        return soup.title.string.strip() or None
    return None


def _meta_description(soup: BeautifulSoup) -> str | None:
    for attrs in (
        {"name": "description"},
        {"property": "og:description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if isinstance(tag, Tag) and tag.get("content"):
            return str(tag["content"]).strip() or None
    return None


def _headings(soup: BeautifulSoup) -> list[str]:
    found: list[str] = []
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(" ", strip=True)
        if text:
            found.append(text)
        if len(found) >= MAX_HEADINGS:
            break
    return found


def _visible_text(soup: BeautifulSoup) -> str:
    raw = soup.get_text(" ", strip=True)
    collapsed = re.sub(r"\s+", " ", raw).strip()
    if len(collapsed) > MAX_TEXT_CHARS:
        return collapsed[:MAX_TEXT_CHARS]
    return collapsed


def _mailto_and_tel(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    emails: list[str] = []
    phones: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = str(tag.get("href") or "").strip()
        lowered = href.lower()
        if lowered.startswith("mailto:"):
            address = href.split(":", 1)[1].split("?", 1)[0].strip()
            if address:
                emails.append(address)
        elif lowered.startswith("tel:"):
            number = href.split(":", 1)[1].strip()
            if number:
                phones.append(number)
    return emails, phones


def _extract_links(soup: BeautifulSoup, base_url: str) -> list[ExtractedLink]:
    page_domain = extract_domain(base_url)
    links: list[ExtractedLink] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href = str(tag.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        normalized = normalize_url(absolute)
        key = normalized or absolute
        if key in seen:
            continue
        seen.add(key)
        anchor = tag.get_text(" ", strip=True)
        path = (normalized or absolute).lower()
        useful = any(token in path or token in anchor.lower() for token in USEFUL_PATH_TOKENS)
        link_domain = extract_domain(normalized or absolute)
        external = bool(page_domain and link_domain and link_domain != page_domain)
        if _host_matches(link_domain, SOCIAL_DOMAINS | VIDEO_DOMAINS) or "wa.me" in path or "whatsapp" in path:
            useful = True
        links.append(
            ExtractedLink(
                url=absolute,
                normalized_url=normalized,
                anchor_text=anchor,
                useful=useful,
                external=external,
            )
        )
        if len(links) >= MAX_LINKS:
            break
    return links


def _emails_from_text(text: str) -> list[str]:
    return [match.group(0).lower() for match in EMAIL_RE.finditer(text)]


def _emails_from_links(links: list[ExtractedLink]) -> list[str]:
    # mailto was skipped in href extraction; emails also appear as visible text.
    return []


def _phones_from_text(text: str) -> list[str]:
    found: list[str] = []
    for match in PHONE_RE.finditer(text):
        raw = match.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if not (10 <= len(digits) <= 15):
            continue
        groups = re.findall(r"\d+", raw)
        if len(groups) >= 5 and max(len(group) for group in groups) <= 2:
            continue
        found.append(re.sub(r"\s+", " ", raw))
    return found


def _phones_from_links(links: list[ExtractedLink]) -> list[str]:
    return []


def _social_from_links(links: list[ExtractedLink]) -> list[str]:
    found: list[str] = []
    for link in links:
        domain = extract_domain(link.normalized_url or link.url)
        if _host_matches(domain, SOCIAL_DOMAINS):
            found.append(link.normalized_url or link.url)
    return found


def _social_from_text(text: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(
        r"https?://(?:www\.)?(?:instagram|facebook|fb)\.com/[^\s<>\"']+",
        text,
        re.IGNORECASE,
    ):
        normalized = normalize_url(match.group(0).rstrip(".,);"))
        if normalized:
            found.append(normalized)
    return found


def _whatsapp_from_links(links: list[ExtractedLink]) -> list[str]:
    found: list[str] = []
    for link in links:
        target = (link.normalized_url or link.url).lower()
        if "wa.me" in target or "whatsapp" in target:
            found.append(link.normalized_url or link.url)
    return found


def _whatsapp_from_text(text: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(
        r"https?://(?:wa\.me|api\.whatsapp\.com|www\.whatsapp\.com)/[^\s<>\"']+",
        text,
        re.IGNORECASE,
    ):
        normalized = normalize_url(match.group(0).rstrip(".,);"))
        if normalized:
            found.append(normalized)
    return found


def _address_candidates(text: str) -> list[str]:
    """Keep short snippets that look like an address; do not invent one."""
    if not text:
        return []
    found: list[str] = []
    for chunk in re.split(r"(?<=[.!?])\s+|\n+", text):
        piece = chunk.strip()
        if len(piece) < 12 or len(piece) > 180:
            continue
        if ECOMMERCE_ADDRESS_NOISE_RE.search(piece):
            continue
        has_pin = bool(PINCODE_RE.search(piece))
        has_hint = bool(ADDRESS_HINT_RE.search(piece))
        has_digit = bool(re.search(r"\d", piece))
        if has_pin or (has_hint and has_digit):
            found.append(piece)
        if len(found) >= MAX_SIGNAL_ITEMS:
            break
    return found


def _city_mentions(text: str) -> list[str]:
    found: list[str] = []
    for city in CITY_NAMES:
        if re.search(rf"\b{re.escape(city)}\b", text, re.IGNORECASE):
            found.append(city)
    return found


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = value.strip()
        if not key:
            continue
        lowered = key.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(key)
    return ordered
