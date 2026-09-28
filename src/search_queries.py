"""Search-query families for physical fashion stockists.

Queries stay plain strings so the existing SearXNG provider can run them.
Each batch item also carries the city, state, and country used later for
geographic verification.
"""

from __future__ import annotations

from geography import ExpectedPlace, known_places

RETAIL_IDENTITY = (
    "designer boutique",
    "multi brand boutique",
    "multi designer store",
    "contemporary fashion boutique",
    "curated fashion store",
    "concept store fashion",
    "fashion concept store",
    "designer collective",
    "independent designer store",
    "curated designer store",
    "fashion destination",
    "lifestyle store fashion",
    "designer showroom",
    "contemporary designer store",
)
POSITIONING = (
    "slow fashion boutique",
    "sustainable fashion store",
    "independent designers",
    "emerging designers",
    "contemporary Indian designers",
    "curated Indian fashion",
    "independent labels",
    "conscious fashion store",
    "artisanal fashion store",
    "handcrafted fashion boutique",
)
PHYSICAL_STORE = (
    'fashion store "visit us"',
    'designer boutique "visit us"',
    'concept store "our store"',
    'designer store "store location"',
    'boutique "opening hours"',
    "fashion store showroom",
    "designer boutique showroom",
)
CONTEXTUAL = (
    "best boutiques {city}",
    "best concept stores {city}",
    "independent designer stores {city}",
    "curated fashion stores {city}",
    "where to shop independent designers {city}",
    "contemporary fashion stores {city}",
    "fashion destinations {city}",
    "slow fashion stores {city}",
    "designer boutiques {city}",
)

FAMILIES = {
    "retail_identity": RETAIL_IDENTITY,
    "positioning": POSITIONING,
    "physical_store": PHYSICAL_STORE,
    "contextual": CONTEXTUAL,
}


def render_query(template: str, place: ExpectedPlace) -> str:
    city = place.city
    if "{city}" in template:
        return template.format(city=city)
    return f"{template} {city}"


def queries_for_place(
    place: ExpectedPlace,
    *,
    families: tuple[str, ...] | None = None,
    per_family: int | None = None,
    limit: int = 5,
) -> list[dict]:
    """Build batch entries the existing multi-query runner already accepts."""
    chosen = families or tuple(FAMILIES)
    specs: list[dict] = []
    for name in chosen:
        templates = FAMILIES[name]
        if per_family is not None:
            templates = templates[:per_family]
        for template in templates:
            specs.append(
                {
                    "query": render_query(template, place),
                    "limit": limit,
                    "family": name,
                    "city": place.city,
                    "state": place.state,
                    "country": place.country,
                }
            )
    return specs


def sample_batch(
    cities: tuple[str, ...] = ("Goa", "Mumbai", "Delhi", "Bengaluru", "Jaipur"),
    *,
    per_family: int = 1,
    limit: int = 3,
) -> list[dict]:
    """A small multi-city batch: one query from each family per city."""
    by_city = {place.city.casefold(): place for place in known_places()}
    specs: list[dict] = []
    for city in cities:
        place = by_city.get(city.casefold())
        if place is None:
            raise KeyError(f"Unknown sample city: {city}")
        specs.extend(queries_for_place(place, per_family=per_family, limit=limit))
    return specs
