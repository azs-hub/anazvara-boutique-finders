"""Turn search hits into classified, normalized, in-search-deduped candidates.

Directory and article candidates are kept. Expanding those pages for extra
boutique links is a later step (see ``expand_from_page``).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urlparse

from classification import ResultType, classify_url
from search_provider import SearchResult
from url_normalization import extract_domain, normalize_url


@dataclass(frozen=True)
class Candidate:
    """A discovery candidate derived from a search hit.

    Attributes:
        title: Search result title.
        url: Original result URL.
        normalized_url: Canonical URL used for comparison.
        domain: Normalized host (no ``www.``).
        snippet: Search snippet.
        result_type: Heuristic class (WEBSITE, DIRECTORY, …).
        search_query: Query that produced this hit.
        search_source: Search-engine name from the provider.
    """

    title: str
    url: str
    normalized_url: str
    domain: str
    snippet: str
    result_type: ResultType
    search_query: str
    search_source: str


def _homepage_path(path: str) -> bool:
    stripped = path.strip("/")
    return stripped == "" or stripped.lower() in {"index", "index.html", "home"}


def website_url_usefulness(url: str) -> int:
    """Higher is better. Prefer a homepage over /about or /contact."""
    parsed = urlparse(url)
    path = parsed.path or ""
    score = 0
    if _homepage_path(path):
        score += 100
    else:
        segments = [part for part in path.split("/") if part]
        score += max(0, 40 - 8 * len(segments))
        score -= min(len(path), 40)
        lowered = path.lower()
        if any(
            token in lowered
            for token in ("/about", "/contact", "/store", "/shop", "/boutique")
        ):
            score += 15
    if parsed.query:
        score -= 10
    if parsed.scheme == "https":
        score += 5
    return score


def candidate_from_search_result(
    result: SearchResult,
    search_query: str,
) -> Candidate | None:
    """Build one candidate from a search hit, or ``None`` if the URL is unusable."""
    normalized = normalize_url(result.url)
    domain = extract_domain(normalized or result.url)
    if not normalized or not domain:
        return None
    result_type = classify_url(normalized)
    return Candidate(
        title=result.title,
        url=result.url,
        normalized_url=normalized,
        domain=domain,
        snippet=result.snippet,
        result_type=result_type,
        search_query=search_query,
        search_source=result.source,
    )


def _dedupe_key(candidate: Candidate) -> tuple[str, str]:
    """WEBSITE rows collapse by domain; other types collapse by exact URL."""
    if candidate.result_type is ResultType.WEBSITE:
        return (ResultType.WEBSITE.value, candidate.domain)
    return (candidate.result_type.value, candidate.normalized_url)


def _prefer_candidate(current: Candidate, challenger: Candidate) -> Candidate:
    if current.result_type is ResultType.WEBSITE:
        current_score = website_url_usefulness(current.normalized_url)
        challenger_score = website_url_usefulness(challenger.normalized_url)
        if challenger_score > current_score:
            return challenger
        if (
            challenger_score == current_score
            and len(challenger.normalized_url) < len(current.normalized_url)
        ):
            return challenger
        return current
    return current


def candidates_from_search_results(
    results: list[SearchResult],
    search_query: str,
) -> list[Candidate]:
    """Classify, normalize, and deduplicate hits from a single search.

    Does not consult the historical SQLite database.
    """
    grouped: dict[tuple[str, str], Candidate] = {}
    order: list[tuple[str, str]] = []

    for result in results:
        candidate = candidate_from_search_result(result, search_query)
        if candidate is None:
            continue
        key = _dedupe_key(candidate)
        if key not in grouped:
            grouped[key] = candidate
            order.append(key)
        else:
            grouped[key] = _prefer_candidate(grouped[key], candidate)

    return [grouped[key] for key in order]


def count_by_type(candidates: list[Candidate]) -> dict[ResultType, int]:
    """Return counts for each ``ResultType`` (missing types are 0)."""
    counts: dict[ResultType, int] = defaultdict(int)
    for candidate in candidates:
        counts[candidate.result_type] += 1
    return {result_type: counts[result_type] for result_type in ResultType}


def expand_from_page(candidate: Candidate) -> list[Candidate]:
    """Later: fetch DIRECTORY/ARTICLE pages and extract boutique links.

    Not implemented in this step. Kept as a stable hook so discovery can grow
    without rewriting classification or search providers.
    """
    raise NotImplementedError(
        "Directory/article page expansion is not implemented yet. "
        f"{candidate.result_type.value} pages are classified and kept only."
    )
