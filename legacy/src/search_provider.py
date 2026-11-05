"""Search-provider interface used by discovery.

The rest of the application should depend on ``SearchProvider`` and
``SearchResult`` only. Concrete engines (SearXNG today, others later) stay
behind this interface so they can be swapped without rewriting scraping,
SQLite, or Excel export.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResult:
    """One web search hit from a search provider.

    Attributes:
        title: Result title.
        url: Result URL.
        snippet: Short text excerpt, if the provider supplies one.
        source: Search-engine or provider name (for example ``google`` or
            ``searxng``).
    """

    title: str
    url: str
    snippet: str
    source: str


class SearchProvider(ABC):
    """Minimal search interface: ``search(query, page) -> list[SearchResult]``."""

    @abstractmethod
    def search(self, query: str, page: int = 1) -> list[SearchResult]:
        """Return search hits for ``query`` on ``page``.

        Implementations must not raise on typical network or parse failures.
        Return an empty list instead and record a human-readable error if needed.
        """
        ...
