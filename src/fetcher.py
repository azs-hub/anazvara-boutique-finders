"""Conservative HTTP GET for public pages.

Does not bypass robots.txt, logins, CAPTCHA, or bot protection.
Does not retry aggressively. Failures are returned as ``FetchResult``
rather than raised.

Limits
------
- ``DEFAULT_TIMEOUT_SECONDS``: 15
- ``DEFAULT_RETRIES``: 0 (one attempt; set to 1 for a single extra try on
  timeout/connection errors only)
- ``DEFAULT_DELAY_SECONDS``: 1.0 between requests on the same fetcher
- ``MAX_HTML_BYTES``: 1_048_576 (1 MiB) — extra bytes are discarded
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

if TYPE_CHECKING:
    from candidates import Candidate

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_RETRIES = 0
DEFAULT_DELAY_SECONDS = 1.0
MAX_HTML_BYTES = 1_048_576
USER_AGENT = "AnazvaraBoutiqueScraper/0.1 (public-page fetch; local research)"
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one GET. ``html`` is omitted or truncated on purpose."""

    requested_url: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    html: str | None
    error: str | None
    fetched: bool
    elapsed_seconds: float
    truncated: bool = False
    robots_allowed: bool | None = None


class PageFetcher:
    """GET public HTML with timeouts, optional robots checks, and a delay."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        max_html_bytes: int = MAX_HTML_BYTES,
        respect_robots: bool = True,
        user_agent: str = USER_AGENT,
        session: requests.Session | None = None,
    ) -> None:
        if retries not in (0, 1):
            raise ValueError("retries must be 0 or 1")
        self.timeout = timeout
        self.retries = retries
        self.delay_seconds = delay_seconds
        self.max_html_bytes = max_html_bytes
        self.respect_robots = respect_robots
        self.user_agent = user_agent
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)
        self.session.headers.setdefault("Accept", "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8")
        self._last_request_at: float | None = None
        self._robots_cache: dict[str, RobotFileParser | bool] = {}

    def fetch(self, target: Candidate | str) -> FetchResult:
        """GET ``target`` (a Candidate or URL). Network failures are not raised."""
        requested = _target_url(target)
        started = time.monotonic()
        if not requested:
            return FetchResult(
                requested_url="",
                final_url=None,
                status_code=None,
                content_type=None,
                html=None,
                error="No URL provided",
                fetched=False,
                elapsed_seconds=0.0,
                robots_allowed=None,
            )

        allowed = self._robots_allowed(requested)
        if allowed is False:
            return FetchResult(
                requested_url=requested,
                final_url=None,
                status_code=None,
                content_type=None,
                html=None,
                error="Disallowed by robots.txt",
                fetched=False,
                elapsed_seconds=time.monotonic() - started,
                robots_allowed=False,
            )

        attempts = 1 + self.retries
        last_error = "Request failed"
        for attempt in range(attempts):
            self._maybe_delay()
            try:
                response = self.session.get(
                    requested,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
            except requests.Timeout:
                last_error = f"Timed out after {self.timeout}s"
                if attempt + 1 < attempts:
                    continue
                return self._failure(requested, started, last_error, robots_allowed=allowed)
            except requests.ConnectionError as exc:
                last_error = f"Connection error: {exc}"
                if attempt + 1 < attempts:
                    continue
                return self._failure(requested, started, last_error, robots_allowed=allowed)
            except requests.RequestException as exc:
                last_error = f"Request failed: {exc}"
                return self._failure(requested, started, last_error, robots_allowed=allowed)

            elapsed = time.monotonic() - started
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            final_url = response.url or requested
            if response.status_code >= 400:
                return FetchResult(
                    requested_url=requested,
                    final_url=final_url,
                    status_code=response.status_code,
                    content_type=content_type or None,
                    html=None,
                    error=f"HTTP {response.status_code}",
                    fetched=False,
                    elapsed_seconds=elapsed,
                    robots_allowed=allowed,
                )
            if not _is_html_type(content_type):
                return FetchResult(
                    requested_url=requested,
                    final_url=final_url,
                    status_code=response.status_code,
                    content_type=content_type or None,
                    html=None,
                    error=f"Unsupported content type: {content_type or 'unknown'}",
                    fetched=False,
                    elapsed_seconds=elapsed,
                    robots_allowed=allowed,
                )

            raw = response.content[: self.max_html_bytes + 1]
            truncated = len(raw) > self.max_html_bytes
            if truncated:
                raw = raw[: self.max_html_bytes]
            html = raw.decode(response.encoding or "utf-8", errors="replace")
            return FetchResult(
                requested_url=requested,
                final_url=final_url,
                status_code=response.status_code,
                content_type=content_type or None,
                html=html,
                error=None,
                fetched=True,
                elapsed_seconds=elapsed,
                truncated=truncated,
                robots_allowed=allowed,
            )

        return self._failure(requested, started, last_error, robots_allowed=allowed)

    def _failure(
        self,
        requested: str,
        started: float,
        error: str,
        *,
        robots_allowed: bool | None,
    ) -> FetchResult:
        return FetchResult(
            requested_url=requested,
            final_url=None,
            status_code=None,
            content_type=None,
            html=None,
            error=error,
            fetched=False,
            elapsed_seconds=time.monotonic() - started,
            robots_allowed=robots_allowed,
        )

    def _maybe_delay(self) -> None:
        if self.delay_seconds <= 0:
            self._last_request_at = time.monotonic()
            return
        if self._last_request_at is not None:
            wait = self.delay_seconds - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _robots_allowed(self, url: str) -> bool | None:
        if not self.respect_robots:
            return None
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return True
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        cached = self._robots_cache.get(robots_url)
        if cached is True:
            return True
        if cached is not None and not isinstance(cached, bool):
            return bool(cached.can_fetch(self.user_agent, url))
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            parser.read()
        except Exception:
            self._robots_cache[robots_url] = True
            return True
        self._robots_cache[robots_url] = parser
        return bool(parser.can_fetch(self.user_agent, url))


def _target_url(target: Candidate | str) -> str:
    if isinstance(target, str):
        return target.strip()
    return (target.normalized_url or target.url or "").strip()


def _is_html_type(content_type: str) -> bool:
    if not content_type:
        return True
    return any(content_type.startswith(prefix) for prefix in HTML_CONTENT_TYPES)
