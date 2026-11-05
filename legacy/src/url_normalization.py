"""URL and host normalization for discovery candidates.

Does not collapse every URL to a domain: business page paths are kept.
No Public Suffix List dependency — ``www.`` is stripped; other subdomains stay.
"""

from __future__ import annotations

from urllib.parse import ParseResult, parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAMS = {
    "gclid",
    "gclsrc",
    "dclid",
    "fbclid",
    "msclkid",
    "twclid",
    "yclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "igsh",
    "_ga",
    "_gl",
    "_hsenc",
    "_hsmi",
    "ref",
    "referrer",
    "spm",
    "scm",
    "si",
    "ncid",
    "icid",
    "mkt_tok",
}


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    if lowered in TRACKING_PARAMS:
        return True
    return any(lowered.startswith(prefix) for prefix in TRACKING_PARAM_PREFIXES)


def _parse_http_url(url: str) -> tuple[ParseResult, str] | None:
    """Parse an http(s) URL, adding ``https://`` when the scheme is missing."""
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme:
        parsed = urlparse(f"https://{raw}")
    if parsed.scheme not in {"http", "https"}:
        return None
    try:
        host = (parsed.hostname or "").lower().strip()
        # Access validates malformed and out-of-range ports.
        parsed.port
    except ValueError:
        return None
    if not host or " " in host:
        return None
    return parsed, host


def extract_host(url: str) -> str | None:
    """Return a lowercase hostname, or ``None`` if the URL has no host."""
    parsed = _parse_http_url(url)
    if parsed is None:
        return None
    _parsed, host = parsed
    return host


def extract_domain(url: str) -> str | None:
    """Return a normalized host: lowercase, no ``www.``, no port.

    ``https://www.example.com/about`` and ``http://example.com/`` both become
    ``example.com``. Other subdomains (``shop.example.com``) are kept.
    """
    host = extract_host(url)
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def normalize_url(url: str) -> str | None:
    """Return a comparable URL with tracking noise removed.

    - Fragments are dropped
    - Host is lowercased and ``www.`` is stripped
    - Default ports are dropped
    - ``http`` is normalized to ``https``
    - Tracking query parameters are removed; remaining params are sorted
    - Trailing slashes on non-root paths are removed
    - The path is otherwise preserved
    """
    parsed_host = _parse_http_url(url)
    if parsed_host is None:
        return None
    parsed, host = parsed_host
    if host.startswith("www."):
        host = host[4:]

    scheme = "https"
    netloc = host
    if parsed.port and parsed.port not in (80, 443):
        netloc = f"{host}:{parsed.port}"

    path = parsed.path or ""
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    if path == "/":
        path = ""

    query_pairs = sorted(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not _is_tracking_param(key)
        ]
    )
    query = urlencode(query_pairs, doseq=True)

    return urlunparse((scheme, netloc, path, "", query, ""))


def is_same_site(root_url: str, other_url: str) -> bool:
    """Compare a site with its bare parent or child subdomain.

    This intentionally does not guess registrable domains from unrelated
    sibling subdomains without a Public Suffix List.
    """
    root = extract_domain(root_url)
    other = extract_domain(other_url)
    if not root or not other:
        return False
    return (
        other == root
        or other.endswith("." + root)
        or root.endswith("." + other)
    )
