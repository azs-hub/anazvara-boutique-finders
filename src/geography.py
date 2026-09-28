"""Generic geographic verification for stockist search batches.

Evidence has to come from the candidate (address, profile, page text,
social handle), not from the search query alone. ``expected_city``,
``expected_state`` and ``expected_country`` are supplied by the batch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FOREIGN_PLACE_RE = re.compile(
    r"\b(ireland|waterford|dublin|united\s+kingdom|\buk\b|london|"
    r"united\s+states|\busa\b|new\s+york|california|paris|france|"
    r"dubai|singapore|sydney|melbourne|toronto|canada|germany|"
    r"italy|spain|amsterdam)\b",
    re.IGNORECASE,
)
FOREIGN_TLDS = {
    ".ie",
    ".uk",
    ".us",
    ".au",
    ".nz",
    ".de",
    ".fr",
    ".it",
    ".es",
    ".ae",
    ".sg",
    ".ca",
}

# Localities are evidence for a city. They are not a second expected city.
_PLACES: dict[str, dict[str, object]] = {
    "goa": {
        "city": "Goa",
        "state": "Goa",
        "country": "India",
        "aliases": ("goa", "north goa", "south goa"),
        "localities": (
            "panaji",
            "panjim",
            "anjuna",
            "assagao",
            "vagator",
            "morjim",
            "mapusa",
            "margao",
            "madgaon",
            "candolim",
            "calangute",
            "parra",
            "aldona",
            "arossim",
            "cansaulim",
            "bardez",
            "siolim",
            "arpora",
            "baga",
            "fontainhas",
        ),
    },
    "mumbai": {
        "city": "Mumbai",
        "state": "Maharashtra",
        "country": "India",
        "aliases": ("mumbai", "bombay"),
        "localities": (
            "bandra",
            "khar",
            "colaba",
            "juhu",
            "worli",
            "lower parel",
            "kemp's corner",
            "kemps corner",
            "andheri",
            "powai",
            "bkc",
            "santacruz",
            "santa cruz",
            "versova",
            "marine drive",
        ),
    },
    "delhi": {
        "city": "Delhi",
        "state": "Delhi",
        "country": "India",
        "aliases": ("delhi", "new delhi"),
        "localities": (
            "hauz khas",
            "meherchand market",
            "shahpur jat",
            "khan market",
            "connaught place",
            "defence colony",
            "greater kailash",
            "gk 1",
            "gk 2",
            "lodhi",
            "mehrauli",
            "vasant vihar",
            "saket",
            "chanakyapuri",
        ),
    },
    "bengaluru": {
        "city": "Bengaluru",
        "state": "Karnataka",
        "country": "India",
        "aliases": ("bengaluru", "bangalore"),
        "localities": (
            "indiranagar",
            "koramangala",
            "ub city",
            "lavelle road",
            "jayanagar",
            "whitefield",
            "mg road",
            "commercial street",
            "sadashivanagar",
        ),
    },
    "jaipur": {
        "city": "Jaipur",
        "state": "Rajasthan",
        "country": "India",
        "aliases": ("jaipur",),
        "localities": (
            "c-scheme",
            "c scheme",
            "malviya nagar",
            "vaishali nagar",
            "raja park",
            "bani park",
            "pink city",
            "mi road",
        ),
    },
    "hyderabad": {
        "city": "Hyderabad",
        "state": "Telangana",
        "country": "India",
        "aliases": ("hyderabad",),
        "localities": ("banjara hills", "jubilee hills", "gachibowli", "hitech city"),
    },
    "pune": {
        "city": "Pune",
        "state": "Maharashtra",
        "country": "India",
        "aliases": ("pune",),
        "localities": ("koregaon park", "kalyani nagar", "fc road"),
    },
    "chennai": {
        "city": "Chennai",
        "state": "Tamil Nadu",
        "country": "India",
        "aliases": ("chennai", "madras"),
        "localities": ("alwarpet", "nungambakkam", "besant nagar", "adyar"),
    },
    "kolkata": {
        "city": "Kolkata",
        "state": "West Bengal",
        "country": "India",
        "aliases": ("kolkata", "calcutta"),
        "localities": ("park street", "hindustan park", "ballygunge", "camac street"),
    },
    "ahmedabad": {
        "city": "Ahmedabad",
        "state": "Gujarat",
        "country": "India",
        "aliases": ("ahmedabad", "amdavad"),
        "localities": ("cg road", "navrangpura", "satellite", "vastrapur"),
    },
}


@dataclass(frozen=True)
class ExpectedPlace:
    """Structured location for one search batch."""

    city: str
    state: str
    country: str = "India"

    def label(self) -> str:
        return ", ".join(part for part in (self.city, self.state, self.country) if part)


def known_places() -> list[ExpectedPlace]:
    seen: list[ExpectedPlace] = []
    keys: set[str] = set()
    for spec in _PLACES.values():
        city = str(spec["city"])
        if city.casefold() in keys:
            continue
        keys.add(city.casefold())
        seen.append(
            ExpectedPlace(city=city, state=str(spec["state"]), country=str(spec["country"]))
        )
    return seen


def _spec_for_token(token: str) -> dict[str, object] | None:
    key = re.sub(r"\s+", " ", token.strip().casefold())
    if not key:
        return None
    if key in _PLACES:
        return _PLACES[key]
    for spec in _PLACES.values():
        aliases = tuple(spec["aliases"])  # type: ignore[arg-type]
        if key in aliases or key == str(spec["city"]).casefold():
            return spec
    return None


def parse_expected_place(
    value: str | ExpectedPlace | dict | None = None,
    *,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
) -> ExpectedPlace | None:
    """Build a place from a label, a dict, or explicit city/state/country."""
    if isinstance(value, ExpectedPlace):
        return value
    if isinstance(value, dict):
        city = city or value.get("city")
        state = state or value.get("state")
        country = country or value.get("country")
        value = None
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    if city is None and parts:
        city = parts[0]
    if state is None and len(parts) >= 2:
        state = parts[1]
    if country is None and len(parts) >= 3:
        country = parts[2]
    if not city and not state:
        return None
    spec = _spec_for_token(city or "")
    if spec is not None:
        return ExpectedPlace(
            city=str(spec["city"]),
            state=state.strip() if state else str(spec["state"]),
            country=country.strip() if country else str(spec["country"]),
        )
    if not city:
        return None
    return ExpectedPlace(
        city=city.strip(),
        state=(state or "").strip(),
        country=(country or "India").strip() or "India",
    )


def resolve_expected_place(
    *,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
    query: str | None = None,
    expected_location: str | None = None,
) -> ExpectedPlace | None:
    """Prefer explicit batch fields, then a city named in the query."""
    explicit = parse_expected_place(
        expected_location,
        city=city,
        state=state,
        country=country,
    )
    if explicit is not None and (city or expected_location or state):
        return explicit
    if query:
        for spec in _PLACES.values():
            names = (str(spec["city"]), *tuple(spec["aliases"]))  # type: ignore[misc]
            for name in names:
                if re.search(rf"\b{re.escape(name)}\b", query, re.IGNORECASE):
                    return ExpectedPlace(
                        city=str(spec["city"]),
                        state=str(spec["state"]),
                        country=str(spec["country"]),
                    )
    return explicit


def _terms(spec: dict[str, object]) -> list[str]:
    terms = [str(spec["city"]), *tuple(spec["aliases"]), *tuple(spec["localities"])]  # type: ignore[misc]
    return list(dict.fromkeys(term.casefold() for term in terms if term))


def _hits(text: str, terms: list[str]) -> list[str]:
    found: list[str] = []
    for term in sorted(terms, key=len, reverse=True):
        if re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE):
            found.append(term)
    return found


def _foreign_tld(url: str | None) -> bool:
    host = ""
    if url:
        lowered = url.lower()
        host = lowered.split("/")[2] if "://" in lowered else lowered.split("/")[0]
        host = host.split(":")[0]
    return any(host.endswith(tld) for tld in FOREIGN_TLDS)


def verify_geography(
    profile: str,
    *,
    expected: ExpectedPlace | None,
    website: str | None = None,
    address: str | None = None,
    city: str | None = None,
) -> tuple[str, list[str]]:
    """Return YES, NO, or UNKNOWN plus evidence strings.

    UNKNOWN means the candidate does not state a conflicting location.
    It is not treated as a confirmed mismatch.
    """
    blob = " ".join(part for part in (profile, address, city) if part)
    evidence: list[str] = []
    foreign_hits = [match.group(0) for match in FOREIGN_PLACE_RE.finditer(blob)]
    foreign_site = _foreign_tld(website)
    if foreign_hits:
        evidence.append(
            "foreign_place:" + ",".join(sorted({hit.casefold() for hit in foreign_hits}))
        )
    if foreign_site:
        evidence.append("foreign_website_tld")

    if expected is None:
        if foreign_hits or foreign_site:
            return "NO", evidence or ["wrong_country"]
        observed = _observed_cities(blob)
        if observed:
            evidence.append("observed_city:" + ",".join(observed))
            return "YES", evidence
        return "UNKNOWN", evidence

    spec = _spec_for_token(expected.city) or {
        "city": expected.city,
        "state": expected.state,
        "country": expected.country,
        "aliases": (expected.city.casefold(),),
        "localities": (),
    }
    home_terms = _terms(spec)
    home_hits = _hits(blob, home_terms)
    other = []
    for place in known_places():
        if place.city.casefold() == expected.city.casefold():
            continue
        other_spec = _place_spec(place.city)
        if other_spec and _hits(blob, _terms(other_spec)):
            other.append(place.city)
    other = [name for name in dict.fromkeys(other) if name.casefold() != expected.city.casefold()]
    address_home = bool(address and _hits(address, home_terms))
    city_home = bool(city and _hits(city, home_terms))
    if home_hits:
        evidence.append("location_in_profile:" + ",".join(home_hits[:6]))
    if address_home:
        evidence.append("address_matches_expected_place")
    if city_home:
        evidence.append("city_field_matches_expected_place")
    if other:
        evidence.append("other_city:" + ",".join(other))

    # A foreign site is a mismatch unless the address or city field itself
    # is in the expected place. A product name that merely contains the
    # city ("Goa Goa collection") is not enough.
    if foreign_site and not (address_home or city_home):
        return "NO", evidence or ["foreign_website_tld"]
    home = bool(home_hits or address_home or city_home)
    if foreign_hits and not home:
        return "NO", evidence or ["wrong_country"]
    if other and not home:
        return "NO", evidence or ["wrong_city"]
    if home:
        return "YES", evidence
    state_name = (expected.state or str(spec.get("state") or "")).strip()
    if state_name and re.search(rf"\b{re.escape(state_name)}\b", blob, re.IGNORECASE):
        if state_name.casefold() not in _shared_states():
            evidence.append("state_match")
            return "YES", evidence
        evidence.append("state_only_shared")
    evidence.append(f"expected:{expected.label()}")
    return "UNKNOWN", evidence


def _shared_states() -> set[str]:
    counts: dict[str, int] = {}
    for spec in _PLACES.values():
        state = str(spec["state"]).casefold()
        counts[state] = counts.get(state, 0) + 1
    return {state for state, count in counts.items() if count > 1}


def _place_spec(city: str) -> dict[str, object] | None:
    return _spec_for_token(city)


def _observed_cities(text: str) -> list[str]:
    found: list[str] = []
    for place in known_places():
        spec = _spec_for_token(place.city)
        if spec and _hits(text, _terms(spec)):
            found.append(place.city)
    return found
