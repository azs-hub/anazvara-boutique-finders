# Anazvara Stockist Finder — Status Review & Rebuild Plan (JS)

_Review date: 3 Nov 2026 · Reviewed: `azs-hub/anazvara-boutique-finders` @ `fa7b621`_
_Note: the two Notion spec pages could not be read (Notion renders client-side). This plan uses `README.md`, `AUDIT_PLAN.md`, `docs/SEARCH_AND_LEAD_SPEC.md` and the code. Paste the Notion text into `docs/` if it contains requirements not covered here._

---

## 1. Where the project actually is

| | |
|---|---|
| Code size | ~16,000 lines Python in `src/`, ~6,300 lines of tests, 19 commits |
| Production pipeline | **None.** `discovery.py` (`BoutiqueDiscovery`) and `scraper.py` are stubs. `main.py` is a SQLite smoke test. Every real run goes through a *benchmark* script (`batch_benchmark.py` → `llm_benchmark.evaluate_candidates`) and then `export_leads.py` reads the benchmark JSON. |
| Persistence | SQLite module exists but is **not wired**. No "find N *new* boutiques" — every run starts from zero. |
| LLM | Ollama/Qwen only on "ambiguous" website records, after ~40 regex rules. |

### The last run's output (`output/stockist_*.csv`)

| Group | Rows |
|---|---|
| **LEAD** | **1** (Rangeela Goa) |
| REVIEW | 71 |
| EXCLUDED | 386 |

- 409 / 458 rows have **no city**; only 52 have an address.
- Junk sources entered the funnel: Vogue Italia, McKinsey, Korea Times, Lemon8, NYC & Vietnamese malls.

### The most important finding

**236 rows (52%) were excluded as `article_or_directory_not_business:MENTIONED_BUSINESS`** — shops *named inside guides* that were never followed up. That pile contains exactly your target shops:

> Sacha's Shop · Sosa's · Paper Boat Collective · Wendell Rodricks Design Space · The Shop by Nana Ki · Alchemy · Atomic 29 · Artist Collections…

These are on your own reference list in `SEARCH_AND_LEAD_SPEC.md`. The pipeline **found them and threw them away**, because the guide page is not the business and nothing resolves the name into a business.

## 2. Diagnosis — why it feels stuck

1. **Wrong primary source.** Many Indian boutiques have no website (or a Shopify store that hides the address). They live on **Google Maps** and **Instagram**. The project bans Maps and only scrapes websites from generic web search, so recall depends on SEO.
2. **Rules doing an LLM's job.** Thousands of lines of regex try to answer "is this a multi-brand physical womenswear shop in Goa?". Each new edge case adds a rule, which causes new false negatives (e.g. Artjuna in REVIEW for `geography_unconfirmed`). This is why progress has slowed.
3. **Precision-first gating with no recovery path.** Anything uncertain is excluded instead of being sent to a cheap next step (resolve, then re-qualify).
4. **No geographic constraint on search.** SearXNG uses default engines with no region or language setting, so global results flood in.
5. **Benchmarks instead of a product.** Effort went into measuring stages rather than one end-to-end command that writes to a database.

**What's worth keeping** (port the ideas, not the code): URL normalization & result-type classification, dedupe key order (domain → IG handle → phone → name+city), the `geography.py` locality catalog, the 5 query families, robots/timeout discipline, and the reference shop list (it becomes your eval set).

## 3. Target design

### Principle
**Collect businesses from many cheap sources → merge into one record per shop → enrich → let one LLM call qualify → simple score → human review.** Don't use rules to reject anything early. Deduplication and the qualify step do the filtering.

```
            ┌─ OSM Overpass (free, structured shops)
            ├─ Google Places Text Search (free tier)
 DISCOVER ──┼─ SearXNG (free, self-hosted)
            ├─ Exa / Tavily (free tiers, semantic)
            └─ Guide expansion (LLM extracts shop names from articles)
                         │
 RESOLVE   name+locality → website / IG / maps / phone   (merge on dedupe keys)
                         │
 ENRICH    homepage + contact/about/stores pages, JSON-LD, socials
                         │
 QUALIFY   1 Ollama call, JSON-schema output (zod)       ← replaces the rule engine
                         │
 SCORE     deterministic weights → LEAD / REVIEW / REJECT
                         │
 EXPORT    CSV / Google Sheet + "new since last run"
```

Every stage reads from and writes to SQLite, so runs can resume and only do new work.

### Sources & cost (verified Nov 2026)

| Source | Use for | Free allowance | Expected Goa cost |
|---|---|---|---|
| **OSM Overpass** | `shop=clothes\|boutique\|fashion` in a bounding box: name, address, sometimes website/phone | Free, no key | $0 |
| **Google Places API (New) Text Search** | Best single source for physical boutiques: address, phone, website, rating | 5,000 Pro calls/mo per SKU since Mar 2025 | $0 (≈150–300 calls per city). **Set a daily quota cap in Cloud Console** — budget alerts don't stop billing |
| **SearXNG** | Broad recall, guides & articles | Self-hosted | $0 |
| **Exa** | Semantic "find shops like X" & guide discovery | $20 signup + $10/mo credits; search ≈ $7/1k | $0 (~1,400 searches/mo) |
| **Tavily** | Search + extract (clean page text) | 1,000 credits/mo, basic search = 1 credit | $0 |
| **Ollama (qwen3 8B/9B)** | Extraction & qualification | Local | $0 (electricity) |
| `deepresearch.tavily.com` | **Manual** seeding / sanity checks, not in the pipeline | UI | $0 |

Every paid-API provider gets a **monthly budget counter in SQLite** that refuses calls once it reaches the free allowance.

Instagram: **don't scrape it** (ToS, blocks). Capture handles from websites, Maps and search results. Review IG manually in the review step.

### Stack (Node / TypeScript)

| Concern | Choice | Why |
|---|---|---|
| Runtime | Node 22 + TypeScript, `pnpm`, `tsx` | You know it; Cursor handles TS well |
| CLI | `commander` | `pnpm cli find --city Goa --new 50` |
| DB | SQLite via `better-sqlite3` + **Drizzle ORM** | Zero-ops, typed, migrations |
| HTTP/crawl | `undici` fetch + `cheerio`; **Crawlee** (`CheerioCrawler`) if you want queueing, retries, robots & rate limits built in | Playwright only as a fallback for JS-only sites |
| Validation | `zod` | LLM output schemas + config |
| LLM | `ollama` npm package with `format: <JSON schema>` (structured outputs) | Valid JSON, no regex parsing |
| Search SDKs | `exa-js`, `@tavily/core`, plain fetch for SearXNG/Overpass/Places | |
| Tests | `vitest` + recorded HTTP fixtures | |
| Later UI | Small Vue 3 review screen over the same SQLite | Your core stack |

### Data model (minimal)

```
businesses   id, name, city, locality, address, lat, lng, website_domain, instagram,
             phone, email, maps_place_id, status(new|qualified|lead|review|rejected|contacted),
             score, first_seen_run, updated_at
sightings    business_id, source(osm|places|searxng|exa|tavily|guide), source_url, query, raw_json
pages        url, business_id, fetched_at, status, text, jsonld
qualification business_id, model, json, created_at
runs         id, city, started_at, params, stats_json
api_budget   provider, month, calls, cost_usd
```

Dedupe = Places `place_id` → website domain → IG handle → normalized phone → normalized name + locality (fuzzy, e.g. `fastest-levenshtein`).

### Qualification schema (one LLM call per business)

```ts
z.object({
  is_real_business: z.boolean(),
  sells_fashion: z.boolean(),
  physical_store: z.enum(["yes","no","unknown"]),
  multi_brand: z.enum(["yes","no","unknown"]),          // stocks other labels/designers
  womenswear: z.enum(["yes","no","unknown"]),
  aesthetic_fit: z.number().int().min(0).max(5),       // contemporary Indian, resort, linen, handloom, slow fashion
  city: z.string().nullable(), locality: z.string().nullable(),
  evidence: z.array(z.string()).max(5),                // short quotes from the input
  reason: z.string()
})
```

Score = weights over these fields plus contactability (phone/email/IG present) and source agreement (seen in ≥2 sources). Thresholds live in config, not in code.

## 4. Phased plan (each item ≈ one Cursor session)

### Phase 0 — Freeze & define "good" (½ day)
- [ ] Tag the Python repo `python-v0`. Move it to `/legacy` (or leave the branch). Don't port it line by line.
- [ ] Create `eval/golden.json`: the 25 reference shops + ~25 known "bad" examples (online-only brands, marketplaces, magazines), each with city and expected outcome.
- [ ] Define success for Goa: **≥ 80% of golden shops found and scored LEAD/REVIEW**, and **≥ 60% precision on the top 50 leads** (manual check).

### Phase 1 — Skeleton & storage (1–2 days)
- [ ] `pnpm init`, TS strict, eslint, vitest, `src/` layout below.
- [ ] Drizzle schema + migrations for the tables above.
- [ ] Config (`zod`): cities → bounding box, localities (port `geography.py` Goa/Mumbai/Delhi/Bengaluru to JSON), provider keys, budgets, thresholds.
- [ ] `Provider` interface: `search(query, ctx) → Sighting[]` + budget guard.

### Phase 2 — High-yield sources first (2 days)
- [ ] **Overpass provider**: clothes/boutique/fashion shops in the city bbox → sightings.
- [ ] **Google Places provider**: Text Search per locality × 3–4 phrasings ("boutique", "designer store", "concept store", "clothing store"), field mask limited to what you need. Hard daily cap.
- [ ] Merge/dedupe service → `businesses`.
- [ ] Run Goa: **check golden-set recall now.** This alone will likely beat the current pipeline.

### Phase 3 — Web & semantic discovery (2 days)
- [ ] SearXNG provider: set `language=en-IN`, choose engines explicitly, port the query families + locality rotation.
- [ ] Exa provider (`type: "auto"`, `userLocation: "IN"`, category/company where useful) + Tavily provider (`country: "india"`).
- [ ] Result-type classifier (port `classification.py` logic): website / social / directory / article / marketplace / maps.
- [ ] **Guide expansion**: for article/directory pages, fetch text (Tavily Extract or own fetch) → LLM extracts `[{name, locality, hints}]` → sightings with `source=guide`. *(This recovers the 236 lost shops.)*

### Phase 4 — Resolve & enrich (2 days)
- [ ] Resolver: for businesses missing website/IG/address, query Places → Exa → SearXNG with `"<name>" <locality> Goa` and attach.
- [ ] Enricher: fetch homepage + up to 3 same-site pages (contact/about/stores/stockists), extract JSON-LD, `og:site_name`, emails, phones, IG/FB/WhatsApp, address. Respect robots, 15 s timeout, 1 req/s per host, 1 MiB cap (keep your current discipline).
- [ ] Shopify trick: `/pages/contact`, `/pages/about`, `/pages/stores`, and `/collections/vendors` or `/products.json` vendor field → **multi-brand evidence for free**.

### Phase 5 — Qualify & score (1–2 days)
- [ ] Ollama client with JSON-schema `format`, temperature 0, one retry on schema failure, prompt + schema versioned.
- [ ] Input pack ≤ 3k tokens: Places data + JSON-LD + page excerpts + guide mentions.
- [ ] Deterministic scorer + config thresholds → `lead | review | rejected`.
- [ ] Cache by content hash, so reruns don't call the LLM again.

### Phase 6 — The product command (1 day)
- [ ] `pnpm cli find --city Goa --new 50`: runs stages until 50 *new* LEAD+REVIEW businesses exist that weren't in previous runs.
- [ ] `pnpm cli export --city Goa` → CSV (and optionally a Google Sheet) with contact columns + `status` for outreach.
- [ ] `pnpm cli eval` → golden-set recall + precision report.

### Phase 7 — Iterate (ongoing)
- [ ] Tune on eval only (never hard-code shop names into queries).
- [ ] Add cities from the catalog.
- [ ] Vue review UI: approve/reject, which feeds back as labelled data.
- [ ] Outreach tracking (contacted / replied / stocking).

## 5. Proposed repo layout

```
src/
  cli.ts                     # commander commands: find, export, eval, budget
  config/                    # zod config, cities.json (bbox + localities)
  db/schema.ts, db/client.ts
  providers/                 # overpass.ts places.ts searxng.ts exa.ts tavily.ts + budget.ts
  discover/queries.ts        # query families + locality rotation
  discover/classifyUrl.ts
  discover/guideExpand.ts
  resolve/merge.ts, resolve/dedupeKeys.ts, resolve/resolver.ts
  enrich/fetcher.ts, enrich/extract.ts, enrich/shopify.ts
  qualify/schema.ts, qualify/prompt.ts, qualify/ollama.ts
  score/score.ts
  export/csv.ts
eval/golden.json
legacy/ (python, read-only)
```

## 6. Risks & guardrails

- **Google Places billing**: needs a billing account. Set per-API daily quotas (e.g. 300/day) on day one.
- **Places ToS**: you may store `place_id` indefinitely; treat other Places content as cache. Fine for internal lead lists, but don't republish it.
- **Local LLM speed**: ~5–15 s per qualification on a 9B model on a laptop. 300 shops ≈ 1 hour, so run it in the background and cache results.
- **Overfitting to Goa**: keep the golden set multi-city once Mumbai is added.

## 7. Suggested first Cursor prompts

1. *"Read PLAN.md and .cursor/rules. Scaffold Phase 1: pnpm TS project, Drizzle SQLite schema from section 3, zod config with cities.json for Goa (port localities from legacy/src/geography.py). Add vitest. No providers yet."*
2. *"Implement src/providers/overpass.ts for Goa's bbox (shop=clothes|boutique|fashion), mapping to Sighting, plus merge.ts using the dedupe key order in PLAN.md. Add a `find --source osm` CLI path and tests with a recorded fixture."*
3. *"Implement the Google Places Text Search provider with field mask, locality × phrasing queries, and an api_budget guard that refuses calls past the configured daily/monthly limits."*
