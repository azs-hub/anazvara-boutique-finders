"""Turn article and directory pages into business candidates.

The page is a discovery source. Named shops are sent onward as their own
records, with the source URL kept as evidence.
"""

from __future__ import annotations

import re
from dataclasses import replace

from business_candidates import (
    UNKNOWN,
    BusinessCandidate,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
    extract_businesses_from_article,
    extract_businesses_from_directory,
    identify_business_candidates,
    merge_in_memory_duplicates,
)
from candidates import Candidate
from classification import ResultType
from content_extraction import (
    BusinessSignals,
    PageEvidence,
    extract_business_signals,
    extract_page_evidence,
)
from entity_quality import looks_like_media_or_listicle
from geography import ExpectedPlace
from structured_evidence import is_roundup_title, looks_generic_name
from url_normalization import extract_domain

INSTAGRAM_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]{2,30})/?",
    re.IGNORECASE,
)
MENTION_RE = re.compile(
    r"(?:(?:^|\n)\s*(?:\d{1,2})[\.\)]\s+|(?:\d{1,2})[\.\)]\s+)"
    r"([A-Z][A-Za-z0-9'’&.+\-]{1,40}(?:\s+[A-Z][A-Za-z0-9'’&.+\-]{1,40}){0,5})"
    r"(?:\s*[-–—:|,]\s*((?:(?!\s\d{1,2}[\.\)]).){0,80}))?"
)
GENERIC_MENTIONS = {
    "shop",
    "shops",
    "store",
    "stores",
    "boutique",
    "boutiques",
    "fashion",
    "directory",
    "guide",
    "shopping",
}
PINCODE_RE = re.compile(r"\b[1-9]\d{5}\b")
SKIP_ANCHORS = {
    "instagram",
    "facebook",
    "website",
    "click here",
    "read more",
    "here",
    "map",
    "directions",
    "visit site",
    "visit website",
    "help",
    "connect",
    "categories",
    "services",
}
LEADING_STOP = {
    "for",
    "what",
    "more",
    "related",
    "you",
    "key",
    "shopping",
    "how",
    "why",
    "where",
    "when",
    "if",
    "this",
    "these",
    "those",
    "from",
    "located",
    "started",
    "looking",
    "are",
    "visit",
    "a",
    "an",
    "and",
    "but",
    "with",
    "also",
    "our",
    "your",
    "their",
    "they",
    "about",
}
EDITORIAL_RE = re.compile(
    r"\b(more like this|related stories|you may also|key takeaways|shopping tips|"
    r"what makes|read more|visit site|click here|best markets|local handicrafts|"
    r"cashew nuts|may also like)\b",
    re.IGNORECASE,
)


def expand_from_page(
    candidate: Candidate | str,
    *,
    page_evidence: PageEvidence | None = None,
    signals: BusinessSignals | None = None,
    html: str | None = None,
    fetcher=None,
    expected: ExpectedPlace | None = None,
) -> list[BusinessCandidate]:
    """Fetch a listing page and return the businesses it names.

    ``page_evidence`` or ``html`` skips the network so tests can inject a page.
    The article or directory itself is not returned as a lead.
    """
    if isinstance(candidate, str):
        from page_inspection import candidate_from_url

        candidate = candidate_from_url(candidate)
    page, page_signals = _load_page(
        candidate,
        page_evidence=page_evidence,
        signals=signals,
        html=html,
        fetcher=fetcher,
    )
    if page is None:
        return []
    rows = []
    for row in (
        *_extract_rows(candidate, page, page_signals),
        *_mentions_from_text(candidate, page, expected=expected),
        *_instagram_businesses(candidate, page),
    ):
        if _is_source_page(row, page):
            continue
        cleaned = _accepted_name(row.business_name)
        if cleaned is None:
            continue
        rows.append(replace(row, business_name=cleaned))
    stamped = [_stamp(row, candidate, page, expected=expected) for row in rows]
    return merge_in_memory_duplicates(stamped)


def _load_page(
    candidate: Candidate,
    *,
    page_evidence: PageEvidence | None,
    signals: BusinessSignals | None,
    html: str | None,
    fetcher,
) -> tuple[PageEvidence | None, BusinessSignals | None]:
    if page_evidence is not None:
        return page_evidence, signals or BusinessSignals()
    if html:
        evidence = extract_page_evidence(
            html,
            source_url=candidate.normalized_url or candidate.url,
        )
        return evidence, extract_business_signals(evidence)
    from fetcher import PageFetcher
    from page_inspection import inspect_candidate

    snapshot = inspect_candidate(fetcher or PageFetcher(), candidate)
    if not snapshot.fetch.fetched:
        return None, None
    return snapshot.evidence, snapshot.signals


def _extract_rows(
    candidate: Candidate,
    page: PageEvidence,
    signals: BusinessSignals | None,
) -> list[BusinessCandidate]:
    page_signals = signals or BusinessSignals()
    media = looks_like_media_or_listicle(
        url=candidate.normalized_url or candidate.url,
        title=page.title or candidate.title,
        text=page.text or "",
        source_type=candidate.result_type.value,
    )
    if candidate.result_type is ResultType.DIRECTORY:
        rows = extract_businesses_from_directory(candidate, page, page_signals)
    elif candidate.result_type is ResultType.ARTICLE or media:
        rows = extract_businesses_from_article(candidate, page, page_signals)
    else:
        rows = identify_business_candidates(candidate, page, page_signals)
    return [row for row in rows if row.business_name not in {None, "", UNKNOWN}]


def _mentions_from_text(
    candidate: Candidate,
    page: PageEvidence,
    *,
    expected: ExpectedPlace | None,
) -> list[BusinessCandidate]:
    text = page.text or ""
    if not text:
        return []
    rows: list[BusinessCandidate] = []
    seen: set[str] = set()
    for match in MENTION_RE.finditer(text):
        name = _clean_mention(match.group(1))
        if name is None or name.casefold() in seen:
            continue
        tail = match.group(2) or ""
        window = text[match.start() : match.start() + 400]
        instagram = _instagram_in(window)
        address = tail.strip() if PINCODE_RE.search(tail) or _looks_like_address(tail) else None
        seen.add(name.casefold())
        rows.append(
            _mention_row(
                candidate,
                page,
                name=name,
                instagram=instagram,
                address=address,
                expected=expected,
                signal="article_mention",
            )
        )
    return rows


def _instagram_businesses(candidate: Candidate, page: PageEvidence) -> list[BusinessCandidate]:
    rows: list[BusinessCandidate] = []
    for link in page.links:
        url = link.normalized_url or link.url or ""
        match = INSTAGRAM_RE.search(url)
        if not match:
            continue
        handle = match.group(1)
        if handle.lower() in {"p", "reel", "reels", "explore", "stories"}:
            continue
        name = _clean_mention(link.anchor_text)
        if name is None:
            continue
        rows.append(
            _mention_row(
                candidate,
                page,
                name=name,
                instagram=f"https://instagram.com/{handle}",
                address=None,
                expected=None,
                signal="article_instagram",
            )
        )
    return rows


def _mention_row(
    candidate: Candidate,
    page: PageEvidence,
    *,
    name: str,
    instagram: str | None,
    address: str | None,
    expected: ExpectedPlace | None,
    signal: str,
) -> BusinessCandidate:
    source = candidate.normalized_url or candidate.url
    return BusinessCandidate(
        business_name=name,
        website=None,
        instagram=instagram,
        facebook=None,
        whatsapp=None,
        phone=None,
        email=None,
        address=address,
        city=expected.city if expected else UNKNOWN,
        source_url=source,
        source_type="ARTICLE" if candidate.result_type is not ResultType.DIRECTORY else "DIRECTORY",
        business_type=BusinessType.UNKNOWN,
        fashion_relevance=Relevance.UNKNOWN,
        women_fashion_relevance=Relevance.UNKNOWN,
        physical_store=PhysicalStore.UNKNOWN,
        evidence={
            "source_url": source,
            "source_type": candidate.result_type.value,
            "name_source": signal,
            "signals": [signal, "mentioned_business"],
            "entity_relationship": "MENTIONED_BUSINESS",
        },
        confidence=Confidence.LOW,
    )


def _stamp(
    row: BusinessCandidate,
    candidate: Candidate,
    page: PageEvidence,
    *,
    expected: ExpectedPlace | None,
) -> BusinessCandidate:
    evidence = dict(row.evidence or {})
    source_url = candidate.normalized_url or candidate.url
    source_type = (
        "directory"
        if candidate.result_type is ResultType.DIRECTORY
        else "article"
        if candidate.result_type is ResultType.ARTICLE
        or looks_like_media_or_listicle(
            url=source_url,
            title=page.title or candidate.title,
            text=(page.text or "")[:400],
        )
        else "page"
    )
    source = {
        "type": source_type,
        "url": source_url,
        "title": page.title or candidate.title or "",
    }
    sources = list(evidence.get("discovery_sources") or [])
    if source not in sources:
        sources.append(source)
    evidence["discovery_sources"] = sources
    evidence["discovered_from"] = source_type
    evidence["discovery_source_url"] = source_url
    evidence["discovery_source_title"] = page.title or candidate.title or ""
    evidence["discovery_source_type"] = source_type
    evidence["entity_relationship"] = evidence.get("entity_relationship") or "MENTIONED_BUSINESS"
    city = row.city
    if (not city or city == UNKNOWN) and expected is not None:
        city = expected.city
    if expected is not None:
        evidence["expected_city"] = expected.city
        evidence["expected_state"] = expected.state
        evidence["expected_country"] = expected.country
    return replace(row, evidence=evidence, city=city or UNKNOWN)


def _is_source_page(row: BusinessCandidate, page: PageEvidence) -> bool:
    if row.business_name in {None, "", UNKNOWN}:
        return True
    signals = list((row.evidence or {}).get("signals") or [])
    if "publisher_not_boutique" in signals or "media_page" in signals:
        return True
    title = (page.title or "").strip()
    if title and row.business_name.strip().casefold() == title.casefold():
        return True
    domain = extract_domain(page.final_url or page.source_url or "") or ""
    if domain and domain.split(".")[0].casefold() in row.business_name.casefold():
        if looks_generic_name(row.business_name) or is_roundup_title(row.business_name):
            return True
    return False


def _accepted_name(value: str | None) -> str | None:
    name = _clean_mention(value)
    if name is None:
        return None
    words = name.split()
    first = words[0].casefold().strip(".,")
    if first in LEADING_STOP:
        return None
    if len(words) == 1 and first in {
        "house",
        "four",
        "from",
        "the",
        "kart",
        "started",
        "located",
        "looking",
        "are",
        "multi",
        "home",
        "store",
        "shop",
    }:
        return None
    if first == "the" and (len(words) < 3 or is_roundup_title(name)):
        return None
    if EDITORIAL_RE.search(name):
        return None
    if re.search(r"\band\b", name, re.IGNORECASE) and len(words) >= 4:
        return None
    if re.fullmatch(r"\d+\+?", name):
        return None
    return name


def _clean_mention(value: str | None) -> str | None:
    if not value:
        return None
    name = re.sub(r"\s+", " ", value).strip(" -–—:|,.")
    name = re.sub(r"^\d{1,2}[\.\)]\s+", "", name).strip()
    if len(name) < 3 or len(name.split()) > 6:
        return None
    if name.casefold() in SKIP_ANCHORS or name.casefold() in GENERIC_MENTIONS:
        return None
    if looks_generic_name(name) or is_roundup_title(name):
        return None
    if not re.search(r"[A-Za-z]", name):
        return None
    return name


def _instagram_in(text: str) -> str | None:
    match = INSTAGRAM_RE.search(text)
    if not match:
        return None
    handle = match.group(1)
    if handle.lower() in {"p", "reel", "reels", "explore", "stories"}:
        return None
    return f"https://instagram.com/{handle}"


def _looks_like_address(text: str) -> bool:
    return bool(
        re.search(
            r"\b(road|street|marg|lane|nagar|assagao|bandra|colaba|hauz khas|indiranagar)\b",
            text,
            re.IGNORECASE,
        )
    )
