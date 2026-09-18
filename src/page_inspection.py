"""Combine a Candidate, fetch, page evidence, and business signals.

Does not identify boutiques, assign scores, follow links, or write to SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass

from candidates import Candidate
from classification import ResultType, classify_url
from content_extraction import (
    BusinessSignals,
    PageEvidence,
    empty_evidence,
    empty_signals,
    extract_business_signals,
    extract_page_evidence,
)
from fetcher import FetchResult, PageFetcher
from url_normalization import extract_domain, normalize_url


@dataclass(frozen=True)
class PageSnapshot:
    """In-memory output of Step 5 for one candidate URL."""

    candidate: Candidate
    fetch: FetchResult
    evidence: PageEvidence
    signals: BusinessSignals


def candidate_from_url(url: str) -> Candidate:
    """Build a manual Candidate so the CLI can inspect a single URL."""
    normalized = normalize_url(url) or url.strip()
    return Candidate(
        title="",
        url=url.strip(),
        normalized_url=normalized,
        domain=extract_domain(normalized) or "",
        snippet="",
        result_type=classify_url(normalized),
        search_query="",
        search_source="manual",
    )


def inspect_candidate(fetcher: PageFetcher, candidate: Candidate) -> PageSnapshot:
    """Fetch the candidate's original URL and extract evidence. No crawling."""
    result_type = candidate.result_type
    fetch = fetcher.fetch(candidate)

    if fetch.fetched and fetch.html:
        evidence = extract_page_evidence(
            fetch.html,
            source_url=candidate.normalized_url or candidate.url,
            final_url=fetch.final_url,
        )
        signals = extract_business_signals(evidence)
        return PageSnapshot(
            candidate=candidate,
            fetch=fetch,
            evidence=evidence,
            signals=signals,
        )

    # SOCIAL / VIDEO (and blocked pages): keep the URL as evidence.
    title = candidate.title or None
    evidence = empty_evidence(candidate.normalized_url or candidate.url, title=title)
    signals = empty_signals()
    if result_type is ResultType.SOCIAL:
        url = candidate.normalized_url or candidate.url
        domain = extract_domain(url) or ""
        if "whatsapp" in domain:
            signals = BusinessSignals(whatsapp_urls=[url])
        elif url:
            signals = BusinessSignals(social_urls=[url])
    return PageSnapshot(
        candidate=candidate,
        fetch=fetch,
        evidence=evidence,
        signals=signals,
    )
