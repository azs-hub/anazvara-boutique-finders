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
    "airbnb.com",
    "booking.com",
    "shopee.com",
    "shopee.in",
    "superstock.com",
    "shutterstock.com",
    "gettyimages.com",
    "unsplash.com",
    "pexels.com",
    "alamy.com",
    "etsy.com",
    "ebay.com",
    "ebay.in",
    "myntra.com",
    "ajio.com",
    "meesho.com",
    "bizgoa.com",
    "bizgoa.in",
    "makemytrip.com",
    "goibibo.com",
    "yatra.com",
    "wheree.com",
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


def is_google_maps_url(url: str | None) -> bool:
    """True for Google Maps / business-profile map links, not google.com search."""
    if not url:
        return False
    normalized = normalize_url(url) or url
    host = extract_host(normalized) or ""
    domain = extract_domain(normalized) or ""
    parsed = urlparse(normalized)
    path = (parsed.path or "").lower()
    if domain in {"maps.google.com", "maps.google.co.in"}:
        return True
    if domain == "goo.gl" and path.startswith("/maps"):
        return True
    if domain in {"google.com", "google.co.in"} and path.startswith("/maps"):
        return True
    if "google." in host and path.startswith("/maps"):
        return True
    return False


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
    if is_google_maps_url(normalized or url):
        return ResultType.DIRECTORY
    if domain == "google.com" and path.startswith("/search"):
        return ResultType.DIRECTORY

    if _host_matches(domain, ARTICLE_DOMAINS):
        return ResultType.ARTICLE

    return ResultType.WEBSITE
