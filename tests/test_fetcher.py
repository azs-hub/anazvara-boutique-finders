"""Fetcher tests using mocked HTTP — no internet."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlparse

import requests

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fetcher import PageFetcher


def _response(
    url: str,
    *,
    status: int = 200,
    content: bytes = b"<html><title>Ok</title></html>",
    content_type: str = "text/html; charset=utf-8",
) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.headers = {"Content-Type": content_type}
    response.url = url
    response.content = content
    response.encoding = "utf-8"
    return response


class FetcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = MagicMock()
        self.fetcher = PageFetcher(
            session=self.session,
            respect_robots=False,
            delay_seconds=0,
            retries=0,
            timeout=2,
        )

    def test_successful_http_response(self) -> None:
        self.session.get.return_value = _response("https://shop.example/")
        result = self.fetcher.fetch("https://shop.example/")
        self.assertTrue(result.fetched)
        self.assertEqual(result.status_code, 200)
        self.assertIn("<title>Ok</title>", result.html or "")
        self.assertIsNone(result.error)
        self.session.get.assert_called_once()
        kwargs = self.session.get.call_args.kwargs
        self.assertTrue(kwargs.get("allow_redirects"))

    def test_redirect_final_url(self) -> None:
        self.session.get.return_value = _response("https://shop.example/home")
        result = self.fetcher.fetch("http://shop.example/old")
        self.assertTrue(result.fetched)
        self.assertEqual(result.requested_url, "http://shop.example/old")
        self.assertEqual(result.final_url, "https://shop.example/home")

    def test_timeout(self) -> None:
        self.session.get.side_effect = requests.Timeout()
        result = self.fetcher.fetch("https://slow.example/")
        self.assertFalse(result.fetched)
        self.assertIsNone(result.html)
        self.assertIn("Timed out", result.error or "")

    def test_connection_failure(self) -> None:
        self.session.get.side_effect = requests.ConnectionError("refused")
        result = self.fetcher.fetch("https://down.example/")
        self.assertFalse(result.fetched)
        self.assertIn("Connection error", result.error or "")

    def test_http_error(self) -> None:
        self.session.get.return_value = _response(
            "https://blocked.example/",
            status=403,
            content=b"forbidden",
        )
        result = self.fetcher.fetch("https://blocked.example/")
        self.assertFalse(result.fetched)
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.error, "HTTP 403")
        self.assertIsNone(result.html)

    def test_content_type_handling(self) -> None:
        self.session.get.return_value = _response(
            "https://files.example/catalog.pdf",
            content=b"%PDF-1.4",
            content_type="application/pdf",
        )
        result = self.fetcher.fetch("https://files.example/catalog.pdf")
        self.assertFalse(result.fetched)
        self.assertIn("Unsupported content type", result.error or "")
        self.assertEqual(result.content_type, "application/pdf")
        self.assertIsNone(result.html)

    def test_xml_allowed_for_sitemaps(self) -> None:
        self.session.get.return_value = _response(
            "https://shop.example/sitemap.xml",
            content=b"<?xml version='1.0'?><urlset></urlset>",
            content_type="application/xml",
        )
        blocked = self.fetcher.fetch("https://shop.example/sitemap.xml")
        self.assertFalse(blocked.fetched)
        allowed = self.fetcher.fetch("https://shop.example/sitemap.xml", allow_xml=True)
        self.assertTrue(allowed.fetched)
        self.assertIn("urlset", allowed.html or "")

    def test_robots_disallow_without_get(self) -> None:
        class DenyParser:
            def can_fetch(self, _agent: str, _url: str) -> bool:
                return False

        fetcher = PageFetcher(
            session=self.session,
            respect_robots=True,
            delay_seconds=0,
        )
        robots_url = "https://private.example/robots.txt"
        fetcher._robots_cache[robots_url] = DenyParser()  # type: ignore[assignment]
        result = fetcher.fetch("https://private.example/secret")
        self.assertFalse(result.fetched)
        self.assertEqual(result.error, "Disallowed by robots.txt")
        self.session.get.assert_not_called()
        self.assertEqual(urlparse(robots_url).netloc, "private.example")


if __name__ == "__main__":
    unittest.main()
