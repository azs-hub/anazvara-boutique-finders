"""Deterministic search-result classification from URL/host/path heuristics.

This is not AI. Directory and article hits are kept — they can later yield
boutique links when page expansion is implemented.
"""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse

from url_normalization import extract_domain, extract_host, normalize_url


class ResultType(str, Enum):
    WEBSITE = "WEBSITE"
    SOCIAL = "SOCIAL"
    DIRECTORY = "DIRECTORY"
    ARTICLE = "ARTICLE"
    VIDEO = "VIDEO"
    UNKNOWN = "UNKNOWN"


SOCIAL_DOMAINS = {
    "instagram.com",
    "facebook.com",
    "fb.com",
    "fb.me",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "pinterest.com",
    "pinterest.co.uk",
    "tiktok.com",
    "threads.net",
    "reddit.com",
}

VIDEO_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "m.youtube.com",
    "vimeo.com",
    "dailymotion.com",
}

DIRECTORY_DOMAINS = {
    "justdial.com",
    "sulekha.com",
    "indiamart.com",
    "tradeindia.com",
    "lbb.in",
    "wanderlog.com",
    "tripadvisor.com",
    "tripadvisor.in",
    "yelp.com",
    "timeout.com",
    "magicpin.in",
    "near.in",
    "asklaila.com",
    "yellowpages.com",
    "yellowpages.in",
    "foursquare.com",
    "zomato.com",
    "urbancompany.com",
    "craftsvilla.com",
    "nykaa.com",
    "amazon.in",
    "amazon.com",
    "flipkart.com",
    "maps.google.com",
    "goo.gl",
}

ARTICLE_DOMAINS = {
    "medium.com",
    "wikipedia.org",
    "en.wikipedia.org",
    "timesofindia.indiatimes.com",
    "indianexpress.com",
    "ndtv.com",
    "hindustantimes.com",
    "vogue.in",
    "vogue.com",
    "elle.in",
    "elle.com",
    "grazia.co.in",
    "lifestyleasia.com",
    "architecturaldigest.in",
    "architecturaldigest.com",
    "cnn.com",
    "bbc.com",
    "bbc.co.uk",
    "theguardian.com",
    "blogspot.com",
}


def _host_matches(domain: str | None, catalog: set[str]) -> bool:
    if not domain:
        return False
    if domain in catalog:
        return True
    return any(domain.endswith(f".{known}") for known in catalog)


def classify_url(url: str) -> ResultType:
    """Classify ``url`` using host and path heuristics only."""
    normalized = normalize_url(url)
    host = extract_host(normalized or url)
    domain = extract_domain(normalized or url)
    if not host or not domain:
        return ResultType.UNKNOWN

    if _host_matches(domain, VIDEO_DOMAINS):
        return ResultType.VIDEO
    if _host_matches(domain, SOCIAL_DOMAINS):
        return ResultType.SOCIAL
    if _host_matches(domain, DIRECTORY_DOMAINS):
        return ResultType.DIRECTORY

    parsed = urlparse(normalized or url)
    path = (parsed.path or "").lower()
    if domain == "google.com" and (
        path.startswith("/maps") or path.startswith("/search")
    ):
        return ResultType.DIRECTORY

    if _host_matches(domain, ARTICLE_DOMAINS):
        return ResultType.ARTICLE

    return ResultType.WEBSITE
