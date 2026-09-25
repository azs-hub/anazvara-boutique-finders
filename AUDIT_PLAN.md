# Audit plan — identification, evidence, enrichment

Audit only. No architecture rewrite. No paid APIs. No Playwright.

## Current architecture

Staged pipeline (unchanged):

```
SearXNG
  → Candidate normalize/classify/dedupe
  → homepage HTTP fetch (robots.txt, timeouts, 1 MiB cap)
  → HTML evidence + contact/address signals
  → optional depth-1 same-site enrichment (≤3 pages, sitemap as URL source)
  → deterministic BusinessCandidate identification
  → in-memory dedupe
  → benchmark JSON / later SQLite + Excel
```

`BoutiqueDiscovery` / `WebsiteScraper.scrape` are still stubs. Historical SQLite exclusion is not wired to identification.

## Relevant files / functions

| Area | File | Functions |
|------|------|-----------|
| Discovery/search | `src/searxng_provider.py`, `src/search_provider.py`, `src/discovery.py` | `SearXNGSearchProvider.search`, `candidates_from_search_results` |
| Candidate inspection | `src/candidates.py`, `src/classification.py`, `src/page_inspection.py` | `classify_url`, `inspect_candidate` |
| HTTP fetcher | `src/fetcher.py` | `PageFetcher.fetch`, `_robots_allowed`, `robots_sitemap_urls` |
| Sitemap | `src/sitemap.py` | `discover_sitemap_urls`, `_ingest_sitemap_docs`, `_fetch_and_parse` |
| Robots | `src/fetcher.py` | `_robots_cache` per origin (already reused in a run) |
| HTML extraction | `src/content_extraction.py` | `extract_page_evidence`, `_page_title`, `_headings`, `_visible_text` |
| JSON-LD | **missing** | No `application/ld+json` parsing exists |
| Business identification | `src/business_candidates.py` | `identify_business_from_page`, `_owner_candidate`, `_website_business_name` |
| Classification | `src/business_candidates.py` | `_classify_business_type`, `_women_fashion_relevance`, `_physical_store`, `_city_for_owner` |
| Deduplication | `src/business_candidates.py`, `src/deduplication.py` | `merge_in_memory_duplicates`, `is_same_boutique` |
| Enrichment | `src/enrichment.py` | `enrich_candidate`, `select_enrichment_candidates`, `url_enrichment_score` |
| Benchmarks | `src/benchmark.py`, `src/enrichment_benchmark.py` | homepage-only vs enriched impact |
| Output | `src/excel_export.py`, `src/database.py` | not used by identification yet |

## Problems found

1. **Names depend on title / H1 / search title.** `_website_business_name` uses `page_evidence.title or candidate.title`, then the first heading. `_page_title` prefers `og:title`, which is often cart/article chrome. There is no `og:site_name` or JSON-LD Organization/LocalBusiness/WebSite name. Generic filters exist (`GENERIC_NAMES`, `CHROME_NAME_RE`) but cannot recover a real brand when the title is rejected.

2. **Structured page data is unused.** Scripts are stripped as noise before any schema parse. Telephone, email, PostalAddress, sameAs, and LocalBusiness type never become provenance-bearing facts. Classification then falls back to keyword counts on visible text.

3. **Business type is hit-count based.** `_classify_business_type` uses `boutique_hits >= 2` (or 1 + name). Independent signals (address, store page, LocalBusiness, visit language, fashion categories) are not combined. That leaves many real shops as `UNKNOWN`.

4. **Women’s fashion and physical store are too conservative, not too loose.** Women stays `UNKNOWN` unless a high regex matches or two medium tokens appear. Physical store requires address+pincode or visit-phrase+address. LocalBusiness schema, store pages, and “find us” / “shop in {city}” are unused. Online-only language already maps to `NO` — keep that.

5. **Enrichment is not selective.** `enrich_candidate` always discovers sitemaps and always selects up to 3 internal pages, even when homepage evidence is already complete, and even when homepage nav already has Contact/Stores/About.

6. **Sitemap cost is the main request amplifier.** For every WEBSITE candidate: robots sitemap seeds (up to 3) and/or `/sitemap.xml` + `/sitemap_index.xml`, then up to 5 child sitemaps. No early stop once Contact/Store/About locs exist. Product children are not deprioritized. Discovery is not cached on the shared `PageFetcher`. Existing test `test_sitemap_index_handling` currently *requires* fetching 5 children.

7. **Enrichment can weaken fields.** Combined identification concatenates all page text. Contact-page titles can outrank homepage identity. Later LOW evidence can replace earlier HIGH structured/homepage evidence. `_merge_pair` keeps the first non-UNKNOWN value, not the stronger source.

8. **Homepage-only `benchmark.py` does not report HTTP/sitemap/robots/runtime or enrichment gain/worse.** `enrichment_benchmark.py` already measures most of this; it needs canonical metrics (avg requests/candidate, records improved, records weakened) made explicit — not a second pipeline.

## Proposed changes (incremental)

- Extract JSON-LD / `og:site_name` / brand meta **before** script stripping; attach `StructuredFact` provenance to `PageEvidence`.
- Rank business names: Organization → LocalBusiness → WebSite → og:site_name → brand meta → homepage H1 → homepage title → search title. Keep generic/transactional/article/nav rejection.
- Classify business type, women’s relevance, and physical store from **evidence combinations**, keeping current enums and existing keyword paths as fallbacks.
- Assess homepage completeness; skip or partially enrich; do not fetch pages merely because they exist.
- Keep sitemap support and the 5-child **cap**; prioritize useful children; stop early; cache discovery per domain on the fetcher.
- Merge fields by source precedence: structured > explicit page text > dedicated page > homepage inference > search snippet.
- Extend `enrichment_benchmark.py` as the canonical end-to-end report. Leave `benchmark.py` in place.

## Risks

- JSON-LD publisher/WebSite names on article-like homepages could be wrong — still run generic-name filters; do not trust a single source.
- Broader physical-store rules could mark pure e-commerce as `YES` — require LocalBusiness/address/visit evidence; keep `online-only` → `NO`.
- Women’s MEDIUM from boutique+weak tokens could over-infer — require an actual women’s indicator, not type alone.
- Sitemap early-stop must not drop the only useful child (pages vs products). Prioritize `pages/contact/store/about`, skip product children once any relevant loc exists.
- Adaptive enrichment must not break tests that rely on Contact/Store/About for weak homepages (`title=Home`).
- Live Mumbai benchmark is network-dependent; unit tests remain the regression gate.

## Tests that will be run

1. `python -m unittest discover -s tests -v` (existing + new).
2. New unit tests: name source priority, generic rejection, JSON-LD facts, type/women/store combinations, adaptive skip/partial enrich, sitemap early-stop/cache, evidence merge (HIGH not replaced by LOW).
3. Canonical enriched benchmark: `python src/enrichment_benchmark.py "women's fashion boutique Mumbai" --limit 100`.
4. Compare against saved homepage-only baseline and the previous enriched run (request volume, UNKNOWN rates, worse-field count). Existing safeguards (robots, same-site, redirect rejection, city non-guessing, chrome names) must still pass.
