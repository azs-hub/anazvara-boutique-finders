"""Search-query families for physical fashion stockists.

Queries stay plain strings so the existing SearXNG provider can run them.
Each batch item also carries the city, state, and country used later for
geographic verification. A locality in the query text does not replace that
expected place.

Recall comes first. These strings collect candidates. They do not decide
whether a business is a stockist, and they do not require the words boutique,
store, fashion, designer, or multi-brand in a result.

The production batch is retail identity, positioning, and discovery language,
each template once against the city, plus one rotated query per extra
location. Physical-retail and editorial queries are built separately.
Family × locality is never fully crossed. ``MAX_QUERIES_PER_PLACE`` is the
hard ceiling.
"""

from __future__ import annotations

from geography import ExpectedPlace, known_places, search_locations

# Retail words a shop might use for itself, including shops that never say
# "boutique".
RETAIL_IDENTITY = (
    "multi brand fashion store",
    "multi brand boutique",
    "multi designer fashion store",
    "multi designer boutique",
    "independent fashion boutique",
    "independent clothing store",
    "curated fashion store",
    "curated clothing store",
    "designer boutique",
    "designer fashion store",
    "concept store fashion",
    "fashion concept store",
    "designer collective",
    "fashion collective",
    "independent designer store",
    "lifestyle store fashion",
    "resort wear boutique",
    "fashion showroom",
)
POSITIONING = (
    "Indian designers fashion store",
    "emerging Indian designers store",
    "independent Indian labels store",
    "curated Indian fashion store",
    "slow fashion store",
    "sustainable fashion boutique",
    "conscious fashion store",
    "artisan fashion store",
    "handcrafted fashion store",
    "handloom clothing store",
    "linen clothing store",
    "natural fabric fashion store",
    "block print clothing store",
    "resort wear fashion store",
    "independent womenswear store",
    "contemporary Indian womenswear store",
)
DISCOVERY_LANGUAGE = (
    "where to shop independent Indian designers",
    "where to shop Indian designer clothing",
    "best independent fashion stores",
    "best multi brand fashion stores",
    "best multi designer stores",
    "best curated fashion stores",
    "best concept stores fashion",
    "fashion shopping independent designers",
    "indie fashion stores",
    "independent clothing shops",
    "unique clothing stores",
    "curated clothing shops",
    "designer shops",
    "fashion destinations",
    "village shops fashion",
)
PHYSICAL_RETAIL = (
    '"visit us" fashion store',
    '"our store" fashion',
    '"store location" fashion',
    '"find us" boutique',
    '"opening hours" fashion store',
    '"physical store" fashion',
    '"shop address" fashion',
    '"retail store" fashion',
    '"showroom" fashion boutique',
)
EDITORIAL = (
    "fashion boutiques to visit",
    "independent fashion stores guide",
    "designer stores guide",
    "where to shop fashion",
    "indie boutique guide",
    "fashion shopping guide",
    "best shops for Indian designers",
)

FAMILIES = {
    "retail_identity": RETAIL_IDENTITY,
    "positioning": POSITIONING,
    "discovery_language": DISCOVERY_LANGUAGE,
    "physical_retail": PHYSICAL_RETAIL,
    "editorial": EDITORIAL,
}
PRODUCTION_FAMILIES = ("retail_identity", "positioning", "discovery_language")
SAMPLED_FAMILIES = ("physical_retail", "editorial")
FAMILY_ALIASES = {
    "physical_store": "physical_retail",
    "contextual": "discovery_language",
}

# Safety ceiling for one place. City-anchored templates are kept first;
# locality queries are what get trimmed.
MAX_QUERIES_PER_PLACE = 80
# Editorial and physical-retail stays a sample, not a second full sweep.
SAMPLED_LOCALITY_LIMIT = 6


def render_query(template: str, place: ExpectedPlace, *, location: str | None = None) -> str:
    label = location or place.city
    if "{city}" in template:
        return template.format(city=label)
    return f"{template} {label}"


def _resolve_families(families: tuple[str, ...] | None) -> tuple[str, ...]:
    chosen = families if families is not None else PRODUCTION_FAMILIES
    resolved: list[str] = []
    for name in chosen:
        canonical = FAMILY_ALIASES.get(name, name)
        if canonical not in FAMILIES:
            raise KeyError(f"Unknown search family: {name}")
        if canonical not in resolved:
            resolved.append(canonical)
    return tuple(resolved)


def _spec(
    template: str,
    place: ExpectedPlace,
    location: str,
    family: str,
    limit: int,
) -> dict:
    return {
        "query": render_query(template, place, location=location),
        "limit": limit,
        "family": family,
        "location": location,
        "city": place.city,
        "state": place.state,
        "country": place.country,
    }


def _dedupe(specs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for spec in specs:
        key = spec["query"].casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(spec)
    return unique


def _cap(city_specs: list[dict], locality_specs: list[dict], max_queries: int | None) -> list[dict]:
    combined = city_specs + locality_specs
    if max_queries is None or len(combined) <= max_queries:
        return combined
    if len(city_specs) >= max_queries:
        return city_specs[:max_queries]
    room = max_queries - len(city_specs)
    return city_specs + locality_specs[:room]


def queries_for_place(
    place: ExpectedPlace,
    *,
    families: tuple[str, ...] | None = None,
    per_family: int | None = None,
    limit: int = 5,
    include_localities: bool = True,
    locality_limit: int | None = None,
    max_queries: int | None = MAX_QUERIES_PER_PLACE,
) -> list[dict]:
    """Build batch entries the existing multi-query runner already accepts.

    Default families are the production set (retail identity, positioning,
    discovery language). Every selected template is run once with the city.
    Each extra alias or locality then receives one rotated template, not the
    full family list. Pass ``families`` to sample physical-retail or editorial
    on their own.
    """
    selected = _resolve_families(families)
    city_specs: list[dict] = []
    pools: dict[str, tuple[str, ...]] = {}
    for name in selected:
        templates = FAMILIES[name]
        if per_family is not None:
            templates = templates[:per_family]
        pools[name] = templates
        for template in templates:
            city_specs.append(_spec(template, place, place.city, name, limit))

    locality_specs: list[dict] = []
    if include_localities and selected and locality_limit != 0:
        extras = [
            label
            for label in search_locations(place)
            if label.casefold() != place.city.casefold()
        ]
        if locality_limit is not None:
            extras = extras[:locality_limit]
        for index, location in enumerate(extras):
            name = selected[index % len(selected)]
            templates = pools[name]
            if not templates:
                continue
            template = templates[(index // len(selected)) % len(templates)]
            locality_specs.append(_spec(template, place, location, name, limit))

    return _cap(_dedupe(city_specs), _dedupe(locality_specs), max_queries)


def production_queries(place: ExpectedPlace, **kwargs) -> list[dict]:
    """Balanced recall batch: identity, positioning, and discovery language."""
    kwargs.setdefault("families", PRODUCTION_FAMILIES)
    return queries_for_place(place, **kwargs)


def sampled_discovery_queries(place: ExpectedPlace, **kwargs) -> list[dict]:
    """Physical-retail and editorial queries, separate from the production batch.

    A hit on these phrases is only a discovery lead. It does not prove a
    physical store or that the page is a shop.
    """
    kwargs.setdefault("families", SAMPLED_FAMILIES)
    kwargs.setdefault("locality_limit", SAMPLED_LOCALITY_LIMIT)
    return queries_for_place(place, **kwargs)


def sample_batch(
    cities: tuple[str, ...] = ("Goa", "Mumbai", "Delhi", "Bengaluru", "Jaipur"),
    *,
    per_family: int = 1,
    limit: int = 3,
    include_localities: bool = False,
) -> list[dict]:
    """A small multi-city batch: a few queries from each family per city.

    Locality rotation stays off so this sample does not multiply into a
    production-sized SearXNG run. Use ``queries_for_place`` for that batch.
    """
    by_city = {place.city.casefold(): place for place in known_places()}
    specs: list[dict] = []
    for city in cities:
        place = by_city.get(city.casefold())
        if place is None:
            raise KeyError(f"Unknown sample city: {city}")
        specs.extend(
            queries_for_place(
                place,
                families=tuple(FAMILIES),
                per_family=per_family,
                limit=limit,
                include_localities=include_localities,
            )
        )
    return specs
