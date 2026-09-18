"""Discovery layer for finding boutique website candidates.

Flow implemented so far:

    SearXNG search
        → SearchResult
        → URL normalization / classification
        → Candidate (this step)
        → future website scraping

Search stays behind ``SearchProvider``. Candidate building lives in
``candidates.py``. Directory and article hits are classified and kept;
``expand_from_page`` is the later hook for extracting boutique links from
those pages (not implemented here).

``BoutiqueDiscovery`` (city + quantity + historical SQLite exclusion) is
still unimplemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from candidates import Candidate, candidates_from_search_results, expand_from_page

__all__ = [
    "BoutiqueDiscovery",
    "Candidate",
    "DiscoveryCandidate",
    "candidates_from_search_results",
    "expand_from_page",
]


@dataclass(frozen=True)
class DiscoveryCandidate:
    """A potential boutique found by a search provider.

    Attributes:
        name: Display name, if the search result provides one.
        city: City used for the search.
        website: Candidate website URL, if available.
        source_url: URL of the search result or listing page.
    """

    name: str | None
    city: str
    website: str | None
    source_url: str | None


class BoutiqueDiscovery:
    """Public-web discovery interface.

    Implementations should use free/public sources only. They must not use
    OpenAI, paid search/scraping APIs, Google Maps scraping, proxies, or
    CAPTCHA-solving services.
    """

    def discover(
        self,
        city: str,
        *,
        max_candidates: int,
    ) -> Iterator[DiscoveryCandidate]:
        """Yield boutique candidates for ``city`` until ``max_candidates``.

        Args:
            city: City to search (for example, ``"Mumbai"``).
            max_candidates: Upper bound on how many candidates to yield.

        Yields:
            DiscoveryCandidate: One search hit at a time so callers can stop
            once enough *new* boutiques have been stored.

        Raises:
            NotImplementedError: Discovery is not implemented yet.
        """
        raise NotImplementedError(
            "Discovery is not implemented in this increment. "
            "The next step will add a free/public search provider."
        )
