# Search and lead spec

This document describes how Anazvara discovery search is supposed to behave.
It is the contract for the SearXNG query layer. It does not change scoring,
deduplication, enrichment, Google Maps, export, or Qwen.

## Purpose of the scraper

The scraper finds physical fashion retailers that could stock Anazvara.
The useful businesses are independent clothing shops, multi-brand and
multi-designer stores, curated and concept stores, designer collectives,
showrooms, and lifestyle shops that sell fashion. Many of them sell
contemporary Indian womenswear, resort wear, linen, handloom, or other
natural fabrics.

Search is responsible for recall. A later stage decides whether a candidate
is actually a stockist.

## What a good candidate looks like

A good candidate is a real shop a person can visit, in the city or region
being searched, selling fashion rather than an unrelated local service.
The strongest fits carry more than one designer or label, or present
themselves as a curated edit, concept store, or collective. A shop can still
be worth collecting when the page is vague about how many labels it stocks.
That question belongs to qualification, not to search.

Directories, magazines, and shopping guides are useful when they name those
shops. The page itself is not the lead. The shops it mentions are.

Online-only brands, wholesalers, marketplaces, and unrelated businesses may
appear in search. They are collected and rejected later if the evidence says so.

## Positive discovery signals

These phrases are search vocabulary. A page that uses them is more likely to
be the kind of shop we want. Using one of them in a query does not prove
anything about a result.

- Retail identity: multi-brand store, multi-designer store, independent
  boutique, curated store, concept store, designer collective, fashion
  collective, lifestyle store, showroom, resort-wear shop.
- Positioning: Indian designers, emerging designers, independent labels,
  slow fashion, sustainable or conscious fashion, artisan and handcrafted
  clothing, handloom, linen, natural fabric, block print, womenswear.
- Everyday language: where to shop, best independent stores, indie shops,
  designer shops, fashion destinations, village shops.
- Physical-retail wording: visit us, our store, store location, find us,
  opening hours, shop address, showroom. These are query phrases only.
- Editorial wording: guides, boutiques to visit, where to shop fashion.

## Physical-store requirements

A stockist lead needs evidence of a place a customer can walk into: an
address, a store or location page, opening hours, visit language, a map
listing, or an equivalent signal already scored by the existing fit logic.

Matching a physical-retail search query does **not** mean the business has
a physical store. Those queries only improve the chance of finding shops
that talk about a premises. Verification still happens after fetch, on the
page evidence, and unknown evidence stays unknown.

## Search families

Query text is generated in `src/search_queries.py`. There are five families:

1. **Retail identity** — what kind of shop it is (store, boutique, collective, concept store, showroom).
2. **Positioning** — whose clothes and what kind (Indian designers, linen, slow fashion, womenswear).
3. **Discovery language** — how people and editors describe shops that do not call themselves boutiques.
4. **Physical retail** — phrases shops use when they have a premises. Sampled separately.
5. **Editorial** — guides and “where to shop” pages that may name obscure shops. Sampled separately.

The production batch is families 1–3. Families 4 and 5 are available through
`sampled_discovery_queries` so a run can add them without folding every
phrase into every search.

Editorial pages are already classifiable as articles, and `expand_from_page`
can pull named businesses off a fetched guide. This search change does not
redesign that extractor. It only makes those pages more likely to be retrieved.

## Locality expansion

Queries are not only `"fashion boutique {city}"`.

For a known place, `search_locations` in `src/geography.py` supplies the city,
then that place's aliases and localities. Goa therefore includes the city
plus labels already in the catalog, such as Panaji, Panjim, Assagao, Anjuna,
Vagator, Morjim, Siolim, Mapusa, Candolim, Calangute, Baga, Parra, Saligao,
Palolem, North Goa, and South Goa. Mumbai, Delhi, Bengaluru, and the other
catalogued cities use their own aliases and localities the same way. A city
that is not in the catalog is searched under its own name only. There is no
Goa-only branch in the generator.

The city on each batch row stays the expected place (`Goa`, `Mumbai`, …)
even when the query text names a neighbourhood. Later geography checks still
use that expected place.

## Why the query count stays bounded

Every template is **not** crossed with every locality.

For one place the production builder:

- runs each selected template once with the city name
- adds one rotated template per extra alias or locality
- stops at `MAX_QUERIES_PER_PLACE` (80), dropping extra locality queries first

`sample_batch` stays smaller: a few templates from each family per city, with
locality rotation off, for the existing multi-city fit benchmark.

A full Goa production batch is the three production families anchored on
"Goa", plus one extra query per Goa alias and locality, and it stays under
the cap. The old generator emitted one city-only query per template and did
not rotate neighbourhoods. The new batch is larger because the vocabulary and
the locality list are both wider, and it is still far below a full
family × locality product.

## Discovery vs verification vs qualification

The pipeline is:

1. **Discovery** — SearXNG runs the generated queries and keeps the hits,
   including articles and directories.
2. **Broad collection** — hits become candidates. Missing the words boutique,
   store, fashion, designer, or multi-brand is not a reason to drop a hit.
3. **Deduplication** — same business found by several queries collapses.
4. **Physical-store verification** — page evidence is scored for a real premises
   and for the expected place.
5. **Qwen qualification** — optional, later, and only where it already runs.
6. **Deterministic scoring** — existing stockist-fit rules choose lead, review,
   or exclude.

A false positive can be rejected in steps 4–6. A shop that never entered
step 1 cannot be recovered.

## Qwen's future role

Qwen via Ollama stays a later classifier for ambiguous website records.
It does not write search queries, and this change does not alter its prompt,
model, or when it is called. Rule fields remain the rule engine's. Qwen
output stays beside them.

## Deduplication principles

Identity is still the existing order: normalized website domain, then
Instagram username, then phone number, then normalized name plus city.
The first present key wins. Search may attach several queries to one
candidate; deduplication unions that provenance instead of keeping a second
copy of the business.

## Benchmark methodology

Compare discovery recall with an external search engine such as Exa or Tavily
on the same city. The external run is a reference for the *kind* of shop the
query vocabulary should be able to surface. It is not a seed list.

Judge the search layer on whether those kinds of shops can appear at all:
independent designers, collectives, concept and lifestyle stores, and shops
described in ordinary language. Judge qualification separately, after fetch.
Do not treat a noisy early candidate set as a failed search if a later stage
can reject it.

## Why Villa Mor matters

Villa Mor is a recall example, not a query. A strategy that only searches
"boutique" can miss it, because a relevant shop may be described as a
village shop, a fashion destination, a collective, or a showroom. Discovery
language exists so that kind of wording still enters the candidate set.
Confirming that Villa Mor itself was found is a benchmark outcome. Typing
its name into a production query is not.

## Reference examples only

These businesses showed up in Exa and Tavily comparisons and illustrate the
target type. They are not production queries and must not be hard-coded into
search logic:

- Sosa's Designer Boutique
- The Flame Store
- 280 Siolim by Savio Jon
- O.M.O.
- Label Zuka
- Mermaid's Boutique / DADAblui
- Rangeela
- People Tree
- Bunti Shop
- Sacha's Shop
- Maya Boutique
- The Shop by Nana Ki
- Villa Mor
- The Good Life Goa
- Syne Goa
- Yellow House Parra
- Paper Boat Collective
- No Nasties
- Artjuna Collection Shop
- Zaia Goa
- COMO Designers Collective
- Republic of Mode
- Fabric Fair
- Noun Goa
- Siroi Goa

Benchmark-only searches that name one of these businesses, including any
Villa Mor fallback in a Goa benchmark harness, stay outside
`src/search_queries.py`.
