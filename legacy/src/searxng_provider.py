"""SearXNG search provider.

Calls a configured SearXNG HTTP ``/search`` endpoint with ``format=json``.
The instance URL comes from ``SEARXNG_URL``; no public instance is hard-coded.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests

from search_provider import SearchProvider, SearchResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TIMEOUT_SECONDS = 15
PROVIDER_NAME = "searxng"
USER_AGENT = "AnazvaraBoutiqueScraper/0.1 (+local discovery test)"


def load_project_env(project_root: Path | None = None) -> None:
    """Load ``KEY=VALUE`` pairs from ``.env`` without overriding existing env vars.

    ``python-dotenv`` is not used; this keeps dependencies unchanged.
    """
    env_path = (project_root or PROJECT_ROOT) / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def parse_searxng_json(payload: dict[str, Any]) -> list[SearchResult]:
    """Parse a SearXNG JSON search payload into ``SearchResult`` rows.

    This function is side-effect free so unit tests can cover it without a
    network request.
    """
    raw_results = payload.get("results") or []
    if not isinstance(raw_results, list):
        return []

    parsed: list[SearchResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or item.get("pretty_url") or "").strip()
        snippet = str(item.get("content") or item.get("snippet") or "").strip()
        engine = item.get("engine")
        if not engine:
            engines = item.get("engines")
            if isinstance(engines, list):
                engine = ", ".join(str(part) for part in engines if part)
            elif engines:
                engine = engines
        source = str(engine).strip() if engine else PROVIDER_NAME
        if not title and not url:
            continue
        parsed.append(
            SearchResult(title=title, url=url, snippet=snippet, source=source)
        )
    return parsed


class SearXNGSearchProvider(SearchProvider):
    """SearchProvider backed by a SearXNG JSON API."""

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.last_error: str | None = None

    @classmethod
    def from_env(cls) -> SearXNGSearchProvider | None:
        """Build a provider from ``SEARXNG_URL``, loading ``.env`` if needed."""
        load_project_env()
        base_url = (os.environ.get("SEARXNG_URL") or "").strip()
        if not base_url:
            return None
        return cls(base_url)

    def search(self, query: str, page: int = 1) -> list[SearchResult]:
        """Request JSON results from SearXNG. Failures return an empty list."""
        self.last_error = None
        endpoint = f"{self.base_url}/search"
        params = {"q": query, "format": "json", "pageno": page}
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

        try:
            response = requests.get(
                endpoint,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.Timeout:
            self.last_error = (
                f"Timed out after {self.timeout}s contacting {endpoint}"
            )
            return []
        except requests.ConnectionError as exc:
            self.last_error = f"Connection error contacting {endpoint}: {exc}"
            return []
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            self.last_error = f"HTTP {status} from {endpoint}: {exc}"
            return []
        except requests.RequestException as exc:
            self.last_error = f"Request failed for {endpoint}: {exc}"
            return []

        try:
            payload = response.json()
        except ValueError as exc:
            self.last_error = f"Invalid JSON from {endpoint}: {exc}"
            return []

        if not isinstance(payload, dict):
            self.last_error = f"Invalid JSON from {endpoint}: expected an object"
            return []

        if payload.get("error"):
            self.last_error = f"SearXNG error: {payload['error']}"
            return []

        return parse_searxng_json(payload)
