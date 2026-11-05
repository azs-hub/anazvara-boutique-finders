"""Bounded iterative SearXNG research for one place.

Round 1 reuses the existing production query families. Later rounds enrich
candidates that are still missing evidence, then search geographic gaps and
editorial pages, then enrich only the businesses those pages newly named.

This is not an agent, not lead scoring, and not a Google Maps or Qwen call.
A Maps phrase below is ordinary search text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable

from business_candidates import (
    UNKNOWN,
    BusinessCandidate,
    BusinessType,
    Confidence,
    PhysicalStore,
    Relevance,
    merge_in_memory_duplicates,
)
from candidates import Candidate, candidates_from_search_results
from classification import ResultType, classify_url
from geography import ExpectedPlace, search_locations
from search_provider import SearchResult
from search_queries import EDITORIAL, production_queries
from structured_evidence import clean_business_name, looks_generic_name

SearchFn = Callable[[str, int], list[SearchResult]]
ExpandFn = Callable[[Candidate], list[BusinessCandidate]]
Clock = Callable[[], str]

INITIAL_DISCOVERY_MAX_QUERIES = 24
CANDIDATE_ENRICHMENT_MAX_QUERIES = 18
EXPANSION_MAX_QUERIES = 12
TOTAL_RESEARCH_MAX_QUERIES = 40
MAX_QUERIES_PER_CANDIDATE = 2
DEFAULT_RESULT_LIMIT = 5

ENRICHMENT_FIELDS = (
    ("address", '"{name}" {city} address'),
    ("instagram", '"{name}" {city} Instagram'),
    ("website", '"{name}" {city} website'),
    ("store", '"{name}" {city} store'),
    ("brands", '"{name}" {city} brands'),
    ("maps", '"{name}" {city} Google Maps'),
)
GAP_TEMPLATES = (
    "independent fashion boutique",
    "multi brand fashion store",
    "concept store fashion",
    "resort wear boutique",
    "independent designers",
)
LOW_VALUE_RE = re.compile(
    r"\b(wholesale|wholesaler|manufacturer|manufacturing|online[\s-]only)\b",
    re.IGNORECASE,
)
FASHION_RE = re.compile(
    r"\b(fashion|boutique|clothing|apparel|designer|wear|collective)\b",
    re.IGNORECASE,
)
MULTI_RE = re.compile(
    r"\b(multi[\s-]?brand|multi[\s-]?designer|independent\s+designers?|"
    r"curated|concept\s+store|collective)\b",
    re.IGNORECASE,
)
ADDRESS_RE = re.compile(
    r"\b\d{1,4}[^.]{0,50}\b(?:road|rd|street|st|marg|lane|nagar)\b[^.]{0,30}",
    re.IGNORECASE,
)
PINCODE_RE = re.compile(r"\b[1-9]\d{5}\b")
INSTAGRAM_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9._]{2,30}/?",
    re.IGNORECASE,
)
BRANDS_RE = re.compile(
    r"\b(brands?\s+(?:we\s+)?(?:carry|stock)|designers?\s+we\s+(?:carry|stock)|"
    r"brand\s+lineup|labels?\s+we\s+stock)\b",
    re.IGNORECASE,
)


@dataclass
class ResearchBudget:
    """Hard caps for one location research job."""

    initial_discovery_max_queries: int = INITIAL_DISCOVERY_MAX_QUERIES
    candidate_enrichment_max_queries: int = CANDIDATE_ENRICHMENT_MAX_QUERIES
    expansion_max_queries: int = EXPANSION_MAX_QUERIES
    total_research_max_queries: int = TOTAL_RESEARCH_MAX_QUERIES
    max_queries_per_candidate: int = MAX_QUERIES_PER_CANDIDATE
    result_limit: int = DEFAULT_RESULT_LIMIT


@dataclass
class ResearchCandidate:
    """Internal research state. Not a CRM or export row."""

    business: BusinessCandidate
    discovery_round: int
    discovery_source: str
    enrichment_status: str = "pending"
    research_queries_run: list[str] = field(default_factory=list)
    last_researched_at: str = ""
    missing_fields: list[str] = field(default_factory=list)
    research_priority: int = 0
    research_id: str = ""


@dataclass
class ResearchReport:
    place: ExpectedPlace
    candidates: list[ResearchCandidate]
    queries: list[dict]
    errors: list[dict] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _room(remaining_total: int, bucket_left: int) -> int:
    return max(0, min(remaining_total, bucket_left))


def _blob(row: ResearchCandidate) -> str:
    evidence = row.business.evidence or {}
    parts = [
        row.business.business_name,
        row.business.address,
        row.business.city,
        row.business.website,
        row.business.instagram,
        evidence.get("snippet"),
        evidence.get("brands_note"),
    ]
    return " ".join(str(part) for part in parts if part)


def _clear_name(name: str | None) -> bool:
    cleaned = clean_business_name(name or "")
    return cleaned != UNKNOWN and not looks_generic_name(cleaned)


def _low_value(row: ResearchCandidate) -> bool:
    return bool(LOW_VALUE_RE.search(_blob(row)))


def _has_address(row: ResearchCandidate) -> bool:
    if (row.business.address or "").strip():
        return True
    return bool(ADDRESS_RE.search(_blob(row)) or PINCODE_RE.search(_blob(row)))


def _physical_weak(row: ResearchCandidate) -> bool:
    if row.business.physical_store is PhysicalStore.YES:
        return False
    return not _has_address(row)


def _brands_known(row: ResearchCandidate) -> bool:
    evidence = row.business.evidence or {}
    if evidence.get("brands_known"):
        return True
    return bool(BRANDS_RE.search(_blob(row)))


def ordered_missing_fields(row: ResearchCandidate) -> list[str]:
    """Enrichment targets still worth a search, most useful first."""
    missing: list[str] = []
    if not _has_address(row):
        missing.append("address")
    if not (row.business.instagram or "").strip():
        missing.append("instagram")
    if not (row.business.website or "").strip():
        missing.append("website")
    if _physical_weak(row):
        missing.append("store")
    if not _brands_known(row):
        missing.append("brands")
    if not _has_address(row):
        missing.append("maps")
    return missing


def research_priority(row: ResearchCandidate) -> int:
    """Who to enrich first. This is not an Anazvara lead score."""
    if not _clear_name(row.business.business_name) or _low_value(row):
        return 0
    if not ordered_missing_fields(row):
        return 0
    text = _blob(row)
    score = 4
    if row.discovery_round and row.enrichment_status != "enriched":
        score += 2
    if (row.business.city or "").strip() and row.business.city != UNKNOWN:
        score += 3
    if FASHION_RE.search(text):
        score += 3
    if not _physical_weak(row):
        score += 2
    if MULTI_RE.search(text):
        score += 2
    if not row.business.website or not row.business.instagram:
        score += 2
    return score


def _refresh(row: ResearchCandidate) -> ResearchCandidate:
    row.missing_fields = ordered_missing_fields(row)
    row.research_priority = research_priority(row)
    if _low_value(row) or not _clear_name(row.business.business_name):
        if row.enrichment_status != "enriched":
            row.enrichment_status = "skipped"
    elif not row.missing_fields and row.enrichment_status == "pending":
        row.enrichment_status = "enriched"
    evidence = dict(row.business.evidence or {})
    evidence["research"] = {
        "discovery_round": row.discovery_round,
        "discovery_source": row.discovery_source,
        "enrichment_status": row.enrichment_status,
        "research_queries_run": list(row.research_queries_run),
        "last_researched_at": row.last_researched_at,
        "missing_fields": list(row.missing_fields),
        "research_priority": row.research_priority,
        "research_id": row.research_id,
    }
    row.business = replace(row.business, evidence=evidence)
    return row


def _query_name(name: str) -> str:
    return name.replace('"', "").strip()


def plan_enrichment_queries(
    row: ResearchCandidate,
    place: ExpectedPlace,
    *,
    limit: int,
    research_round: int,
) -> list[dict]:
    """Search only missing fields, and never more than ``limit`` queries."""
    _refresh(row)
    if row.research_priority <= 0 or limit <= 0:
        return []
    already = {query.casefold() for query in row.research_queries_run}
    room = max(0, limit)
    specs: list[dict] = []
    templates = dict(ENRICHMENT_FIELDS)
    for purpose in row.missing_fields:
        template = templates.get(purpose)
        if template is None:
            continue
        query = template.format(name=_query_name(row.business.business_name), city=place.city)
        if query.casefold() in already:
            continue
        specs.append(
            _job_spec(
                query,
                place,
                family="candidate_enrichment",
                purpose=purpose,
                research_round=research_round,
                location=place.city,
                candidate_id=row.research_id,
            )
        )
        if len(specs) >= room:
            break
    return specs


def _neighbourhoods(place: ExpectedPlace) -> list[str]:
    city = place.city.casefold()
    labels: list[str] = []
    for label in search_locations(place):
        if label.casefold() == city:
            continue
        if re.search(rf"\b{re.escape(place.city)}\b", label, re.IGNORECASE):
            continue
        labels.append(label)
    return labels


def covered_localities(rows: list[ResearchCandidate], place: ExpectedPlace) -> set[str]:
    blob = " ".join(_blob(row) for row in rows)
    blob = f"{blob} " + " ".join(
        str(source.get("query") or "")
        for row in rows
        for source in (row.business.evidence or {}).get("discovery_sources") or []
        if isinstance(source, dict)
    )
    found: set[str] = set()
    for label in _neighbourhoods(place):
        if re.search(rf"\b{re.escape(label)}\b", blob, re.IGNORECASE):
            found.add(label.casefold())
    return found


def plan_expansion_queries(
    place: ExpectedPlace,
    rows: list[ResearchCandidate],
    *,
    limit: int,
    already_run: set[str] | None = None,
) -> list[dict]:
    """One query per uncovered neighbourhood, plus a short editorial sample."""
    if limit <= 0:
        return []
    seen = {query.casefold() for query in (already_run or set())}
    covered = covered_localities(rows, place)
    gaps = [label for label in _neighbourhoods(place) if label.casefold() not in covered]
    locality_budget = min(len(gaps), max(1, limit // 2)) if gaps else 0
    if locality_budget > limit:
        locality_budget = limit
    editorial_budget = limit - locality_budget
    specs: list[dict] = []
    for index, label in enumerate(gaps[:locality_budget]):
        template = GAP_TEMPLATES[index % len(GAP_TEMPLATES)]
        query = f"{template} {label} {place.city}"
        if query.casefold() in seen:
            continue
        seen.add(query.casefold())
        specs.append(
            _job_spec(
                query,
                place,
                family="discovery_expansion",
                purpose="locality_gap",
                research_round=3,
                location=label,
            )
        )
    for template in EDITORIAL:
        if len([item for item in specs if item["purpose"] == "editorial"]) >= editorial_budget:
            break
        query = f"{template} {place.city}"
        if query.casefold() in seen:
            continue
        seen.add(query.casefold())
        specs.append(
            _job_spec(
                query,
                place,
                family="editorial",
                purpose="editorial",
                research_round=3,
                location=place.city,
            )
        )
    return specs[:limit]


def plan_initial_queries(place: ExpectedPlace, *, limit: int) -> list[dict]:
    """A diverse slice of the current production batch. Does not replace it."""
    if limit <= 0:
        return []
    specs = production_queries(place, max_queries=None)
    city_key = place.city.casefold()
    city_specs = [item for item in specs if str(item.get("location") or "").casefold() == city_key]
    local_specs = [item for item in specs if item not in city_specs]
    locality_slots = min(len(local_specs), max(0, limit // 3))
    chosen = _interleave(city_specs, limit - locality_slots)
    chosen.extend(_interleave(local_specs, locality_slots))
    seen = {item["query"].casefold() for item in chosen}
    if len(chosen) < limit:
        for item in specs:
            if item["query"].casefold() in seen:
                continue
            chosen.append(item)
            seen.add(item["query"].casefold())
            if len(chosen) >= limit:
                break
    stamped = []
    for item in chosen[:limit]:
        stamped.append(
            _job_spec(
                item["query"],
                place,
                family=str(item.get("family") or "initial_discovery"),
                purpose="initial_discovery",
                research_round=1,
                location=str(item.get("location") or place.city),
                limit=int(item.get("limit") or DEFAULT_RESULT_LIMIT),
            )
        )
    return stamped


def _interleave(specs: list[dict], limit: int) -> list[dict]:
    if limit <= 0:
        return []
    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    for spec in specs:
        family = str(spec.get("family") or "")
        if family not in buckets:
            buckets[family] = []
            order.append(family)
        buckets[family].append(spec)
    chosen: list[dict] = []
    while len(chosen) < limit and any(buckets.values()):
        for family in order:
            pool = buckets[family]
            if not pool:
                continue
            chosen.append(pool.pop(0))
            if len(chosen) >= limit:
                break
    return chosen


def _job_spec(
    query: str,
    place: ExpectedPlace,
    *,
    family: str,
    purpose: str,
    research_round: int,
    location: str,
    limit: int = DEFAULT_RESULT_LIMIT,
    candidate_id: str = "",
) -> dict:
    return {
        "query": query,
        "limit": limit,
        "family": family,
        "purpose": purpose,
        "round": research_round,
        "location": location,
        "city": place.city,
        "state": place.state,
        "country": place.country,
        "candidate_id": candidate_id,
    }


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return f"r{self._n}"


def _blank_business(**kwargs) -> BusinessCandidate:
    payload = dict(
        business_name=UNKNOWN,
        website=None,
        instagram=None,
        facebook=None,
        whatsapp=None,
        phone=None,
        email=None,
        address=None,
        city=UNKNOWN,
        source_url="",
        source_type="WEBSITE",
        business_type=BusinessType.UNKNOWN,
        fashion_relevance=Relevance.UNKNOWN,
        women_fashion_relevance=Relevance.UNKNOWN,
        physical_store=PhysicalStore.UNKNOWN,
        evidence={},
        confidence=Confidence.LOW,
    )
    payload.update(kwargs)
    return BusinessCandidate(**payload)


def _append_source(evidence: dict, source: dict) -> None:
    sources = [item for item in evidence.get("discovery_sources") or [] if isinstance(item, dict)]
    key = (str(source.get("type") or ""), str(source.get("url") or source.get("query") or ""))
    for item in sources:
        item_key = (str(item.get("type") or ""), str(item.get("url") or item.get("query") or ""))
        if item_key == key:
            item.setdefault("round", source.get("round"))
            item.setdefault("query", source.get("query"))
            evidence["discovery_sources"] = sources
            return
    sources.append(source)
    evidence["discovery_sources"] = sources


def _from_search_hit(
    hit: Candidate,
    *,
    place: ExpectedPlace,
    research_round: int,
    ids: _Ids,
) -> ResearchCandidate | None:
    name = clean_business_name(hit.title)
    if name == UNKNOWN:
        return None
    website = hit.normalized_url if hit.result_type is ResultType.WEBSITE else None
    instagram = hit.normalized_url if hit.result_type is ResultType.SOCIAL and "instagram.com" in hit.domain else None
    evidence = {
        "snippet": hit.snippet,
        "signals": [],
        "discovered_by_queries": list(hit.discovered_by_queries or ((hit.search_query,) if hit.search_query else ())),
    }
    _append_source(
        evidence,
        {
            "type": "search",
            "url": hit.normalized_url,
            "query": hit.search_query,
            "round": research_round,
            "title": hit.title,
        },
    )
    business = _blank_business(
        business_name=name,
        website=website,
        instagram=instagram,
        city=place.city,
        source_url=hit.normalized_url,
        source_type=hit.result_type.value,
        evidence=evidence,
    )
    row = ResearchCandidate(
        business=business,
        discovery_round=research_round,
        discovery_source=hit.search_query or hit.normalized_url,
        research_id=ids.next(),
    )
    return _refresh(row)


def _from_extracted(
    business: BusinessCandidate,
    *,
    place: ExpectedPlace,
    research_round: int,
    query: str,
    ids: _Ids,
) -> ResearchCandidate | None:
    if not _clear_name(business.business_name):
        return None
    evidence = dict(business.evidence or {})
    source_url = str(evidence.get("discovery_source_url") or business.source_url or "")
    _append_source(
        evidence,
        {
            "type": str(evidence.get("discovered_from") or "article"),
            "url": source_url,
            "query": query,
            "round": research_round,
            "title": str(evidence.get("discovery_source_title") or ""),
        },
    )
    city = business.city if business.city and business.city != UNKNOWN else place.city
    stamped = replace(business, evidence=evidence, city=city)
    row = ResearchCandidate(
        business=stamped,
        discovery_round=research_round,
        discovery_source=source_url or query,
        research_id=ids.next(),
    )
    return _refresh(row)


def _iter_research(evidence: dict | None) -> list[dict]:
    if not isinstance(evidence, dict):
        return []
    found: list[dict] = []
    blob = evidence.get("research")
    if isinstance(blob, dict):
        found.append(blob)
    for item in evidence.get("merged_evidence") or []:
        if isinstance(item, dict):
            found.extend(_iter_research(item))
    return found


def _combine(business: BusinessCandidate, members: list[ResearchCandidate]) -> ResearchCandidate:
    if not members:
        row = ResearchCandidate(
            business=business,
            discovery_round=1,
            discovery_source=business.source_url,
            research_id="r0",
        )
        return _refresh(row)
    earliest = min(members, key=lambda item: (item.discovery_round, item.research_id))
    queries: list[str] = []
    for member in members:
        for query in member.research_queries_run:
            if query not in queries:
                queries.append(query)
    timestamps = [member.last_researched_at for member in members if member.last_researched_at]
    status = "pending"
    if any(member.enrichment_status == "enriched" or member.research_queries_run for member in members):
        status = "enriched"
    elif all(member.enrichment_status == "skipped" for member in members):
        status = "skipped"
    row = ResearchCandidate(
        business=business,
        discovery_round=earliest.discovery_round,
        discovery_source=earliest.discovery_source,
        enrichment_status=status,
        research_queries_run=queries,
        last_researched_at=max(timestamps) if timestamps else "",
        research_id=earliest.research_id,
    )
    return _refresh(row)


def dedupe_research_candidates(rows: list[ResearchCandidate]) -> list[ResearchCandidate]:
    """Collapse repeats with the existing in-memory business merge."""
    if not rows:
        return []
    by_id: dict[str, ResearchCandidate] = {}
    businesses: list[BusinessCandidate] = []
    for row in rows:
        _refresh(row)
        by_id[row.research_id] = row
        businesses.append(row.business)
    merged = merge_in_memory_duplicates(businesses)
    combined: list[ResearchCandidate] = []
    used: set[str] = set()
    for business in merged:
        members: list[ResearchCandidate] = []
        for blob in _iter_research(business.evidence):
            research_id = str(blob.get("research_id") or "")
            if research_id and research_id in by_id and research_id not in used:
                members.append(by_id[research_id])
                used.add(research_id)
        combined.append(_combine(business, members))
    return combined


def _address_from(text: str) -> str | None:
    match = ADDRESS_RE.search(text)
    if match:
        return re.sub(r"\s+", " ", match.group(0)).strip(" ,.;")
    pin = PINCODE_RE.search(text)
    if not pin:
        return None
    start = max(0, pin.start() - 40)
    return re.sub(r"\s+", " ", text[start : pin.end()]).strip(" ,.;")


def _harvest(row: ResearchCandidate, results: list[SearchResult]) -> None:
    evidence = dict(row.business.evidence or {})
    name = row.business.business_name.casefold()
    for result in results:
        text = " ".join(part for part in (result.title, result.snippet, result.url) if part)
        instagram = INSTAGRAM_RE.search(result.url or "") or INSTAGRAM_RE.search(result.snippet or "")
        if instagram and not row.business.instagram:
            row.business.instagram = instagram.group(0)
        if not row.business.address:
            found = _address_from(text)
            if found:
                row.business.address = found
        if not row.business.website and name and name in (result.title or "").casefold():
            kind = classify_url(result.url)
            if kind is ResultType.WEBSITE:
                row.business.website = result.url
        if BRANDS_RE.search(text):
            evidence["brands_known"] = True
            evidence["brands_note"] = (result.snippet or "")[:240]
        _append_source(
            evidence,
            {
                "type": "enrichment_result",
                "url": result.url,
                "query": "",
                "round": row.discovery_round,
                "title": result.title,
            },
        )
    row.business.evidence = evidence


def _name_key(row: ResearchCandidate) -> str:
    return re.sub(r"\s+", " ", (row.business.business_name or "").strip().casefold())


def run_research_loop(
    place: ExpectedPlace,
    search_fn: SearchFn,
    *,
    budget: ResearchBudget | None = None,
    expand_page: ExpandFn | None = None,
    now: Clock | None = None,
) -> ResearchReport:
    """Run the four rounds. ``search_fn`` is injected so tests stay offline."""
    limits = budget or ResearchBudget()
    clock = now or _now
    ids = _Ids()
    report = ResearchReport(place=place, candidates=[], queries=[])
    spent = 0
    enrichment_spent = 0
    seen_queries: set[str] = set()
    pool: list[ResearchCandidate] = []

    def issue(specs: list[dict], *, absorb: str) -> None:
        nonlocal spent, enrichment_spent, pool
        for spec in specs:
            if spent >= limits.total_research_max_queries:
                return
            query = str(spec["query"])
            if query.casefold() in seen_queries:
                continue
            seen_queries.add(query.casefold())
            spent += 1
            if absorb == "enrich":
                enrichment_spent += 1
            report.queries.append(spec)
            try:
                results = list(search_fn(query, int(spec.get("limit") or limits.result_limit)) or [])
            except Exception as exc:
                report.errors.append({"stage": "search", "query": query, "error": str(exc)})
                results = []
            if absorb == "enrich":
                target = next(
                    (row for row in pool if row.research_id == spec.get("candidate_id")),
                    None,
                )
                if target is not None:
                    target.research_queries_run.append(query)
                    target.enrichment_status = "enriched"
                    target.last_researched_at = clock()
                    evidence = dict(target.business.evidence or {})
                    _append_source(
                        evidence,
                        {
                            "type": "enrichment_query",
                            "url": "",
                            "query": query,
                            "round": spec.get("round"),
                            "title": target.business.business_name,
                        },
                    )
                    target.business.evidence = evidence
                    _harvest(target, results)
                    _refresh(target)
                continue
            incoming = _collect_hits(
                results,
                query=query,
                research_round=int(spec["round"]),
                place=place,
                ids=ids,
                expand_page=expand_page,
                errors=report.errors,
            )
            pool = dedupe_research_candidates([*pool, *incoming])

    initial_room = _room(
        limits.total_research_max_queries - spent,
        limits.initial_discovery_max_queries,
    )
    issue(plan_initial_queries(place, limit=initial_room), absorb="discover")

    def enrich(research_round: int, rows: list[ResearchCandidate]) -> None:
        room = _room(
            limits.total_research_max_queries - spent,
            limits.candidate_enrichment_max_queries - enrichment_spent,
        )
        if room <= 0:
            return
        ranked = sorted(rows, key=lambda row: (-research_priority(row), row.business.business_name.casefold()))
        planned: list[dict] = []
        for row in ranked:
            per_candidate = max(0, limits.max_queries_per_candidate - len(row.research_queries_run))
            if per_candidate <= 0:
                continue
            chunk = plan_enrichment_queries(
                row,
                place,
                limit=min(per_candidate, room - len(planned)),
                research_round=research_round,
            )
            planned.extend(chunk)
            if len(planned) >= room:
                break
        issue(planned[:room], absorb="enrich")

    enrich(2, list(pool))
    known_names = {_name_key(row) for row in pool}
    expansion_room = _room(limits.total_research_max_queries - spent, limits.expansion_max_queries)
    issue(
        plan_expansion_queries(
            place,
            pool,
            limit=expansion_room,
            already_run=seen_queries,
        ),
        absorb="discover",
    )
    fresh = [row for row in pool if _name_key(row) not in known_names]
    enrich(4, fresh)
    report.candidates = pool
    return report


def _collect_hits(
    results: list[SearchResult],
    *,
    query: str,
    research_round: int,
    place: ExpectedPlace,
    ids: _Ids,
    expand_page: ExpandFn | None,
    errors: list[dict],
) -> list[ResearchCandidate]:
    found: list[ResearchCandidate] = []
    for hit in candidates_from_search_results(results, query):
        if hit.result_type in {ResultType.ARTICLE, ResultType.DIRECTORY} and expand_page is not None:
            try:
                extracted = list(expand_page(hit) or [])
            except Exception as exc:
                errors.append({"stage": "expand", "query": query, "error": str(exc)})
                extracted = []
            for business in extracted:
                row = _from_extracted(
                    business,
                    place=place,
                    research_round=research_round,
                    query=query,
                    ids=ids,
                )
                if row is not None:
                    found.append(row)
            continue
        row = _from_search_hit(hit, place=place, research_round=research_round, ids=ids)
        if row is not None:
            found.append(row)
    return found
