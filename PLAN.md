# Anazvara Stockist Finder — Status Review & Rebuild Plan (JS)

_Synced with spec v1.1 on 5 Nov 2026. The Notion specs linked in `CLAUDE.md` are the product source of truth; this file holds the phase plan and repo status._
_Sections 1–2 are the 3 Nov 2026 review of the Python version (`azs-hub/anazvara-boutique-finders` @ `fa7b621`)._

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
**Collect businesses from many cheap sources → merge into one record per shop → enrich → verify the physical store → let one LLM call fill the signals → transparent rules score → human review.** Don't use rules to reject anything early.

```
            ┌─ OSM Overpass (free, structured shops)
            ├─ Google Places Text Search (free tier)
 DISCOVER ──┼─ SearXNG (free, self-hosted)
            ├─ Exa / Tavily (free tiers, semantic)
            └─ Guide expansion (LLM extracts shop names from articles)
                         │
 RESOLVE   sightings → one business (merge on dedupe keys)
                         │
 ENRICH    light pass for all (homepage + contact/about/stores); full pass once physical store = YES
                         │
 VERIFY    Maps + website evidence → physical_store + confidence (only NO excludes automatically)
                         │
 QUALIFY   1 Ollama call: each signal YES/NO/UNKNOWN + quote + source URL
                         │
 SCORE     config rules → score, fit, priority, system_decision (lead/review/excluded)
                         │
 EXPORT    CRM-ready CSV + "new since last run"
```

Every stage reads from and writes to SQLite, so runs can resume and only do new work.

### Markets and dynamic location
- The search covers India and other markets (Europe). `--location` takes a city, region or country, resolved from `config/places.json`: name, aliases, type, country, bbox, neighbourhoods, languages, currency.
- A country expands into regions and cities; a city expands into neighbourhoods. Queries use the market's language as well as English.
- Signals, query language, currency and price band are configured per market (`config/markets.json`, `config/anazvara-profile.json`). Adding a market needs no code change.

### Sources & cost (verified Nov 2026)

| Source | Use for | Free allowance | Expected Goa cost |
|---|---|---|---|
| **OSM Overpass** | `shop=clothes\|boutique\|fashion` in a bounding box: name, address, sometimes website/phone | Free, no key | $0 |
| **Google Places API (New) Text Search** | Best single source for physical boutiques | 5,000 Pro calls/mo per SKU since Mar 2025 | $0 (≈150–300 calls per city). **Set a daily quota cap in Cloud Console** — budget alerts don't stop billing |
| **SearXNG** | Broad recall, guides & articles | Self-hosted | $0 |
| **Exa** | Semantic "find shops like X" & guide discovery | $20 signup + $10/mo credits; search ≈ $7/1k | $0 (~1,400 searches/mo) |
| **Tavily** | Search + extract (clean page text) | 1,000 credits/mo, basic search = 1 credit | $0 |
| **Ollama (qwen3 8B/9B)** | Extraction & qualification | Local | $0 (electricity) |
| `deepresearch.tavily.com` | **Manual** seeding / sanity checks, not in the pipeline | UI | $0 |

Every paid-API provider gets **usage counters in SQLite** (`api_usage`, one row per provider per day) and calls are refused once it reaches the free allowance. A monthly limit is checked against the sum of that month's rows.

Instagram: **don't scrape it** (ToS, blocks). Capture handles from websites, Maps and search results. Review IG manually in the review step.

### Stack (Node / TypeScript)

| Concern | Choice | Why |
|---|---|---|
| Runtime | Node 22 + TypeScript 6, pnpm 12 workspaces, `tsx` | You know it; Cursor handles TS well |
| CLI | `commander` | `pnpm cli find --location Goa --new 50` |
| DB | SQLite via `better-sqlite3` + **Drizzle ORM** | Zero-ops, typed, migrations |
| HTTP/crawl | `undici` fetch + `cheerio`; **Crawlee** (`CheerioCrawler`) if you want queueing, retries, robots & rate limits built in | Playwright only as a fallback for JS-only sites |
| Validation | `zod` | LLM output schemas + config |
| LLM | `ollama` npm package with `format: <JSON schema>` (structured outputs) | Valid JSON, no regex parsing |
| Search SDKs | `exa-js`, `@tavily/core`, plain fetch for SearXNG/Overpass/Places | |
| Tests | `vitest` + recorded HTTP fixtures | |
| Later UI | Next.js / React CRM in `apps/web` over the same SQLite | Tech spec §1, §5.7 |

### Data model

Built in Phase 1 (schema in `src/db/schema.ts`; database file defaults to `data/stockists.db`):

```
runs            id, city, started_at, finished_at, params, stats_json
businesses      id, name, city, locality, address, lat, lng, website_domain, instagram,
                phone, email, maps_place_id, status(new|qualified|lead|review|rejected|contacted),
                score, first_seen_run, updated_at
sightings       id, business_id (null until merged), source(osm|places|searxng|exa|tavily|guide),
                source_url, query, raw_json
pages           id, url, business_id, fetched_at, status, text, jsonld
qualifications  id, business_id, model, json, created_at
api_usage       provider, day, calls, cost_usd   (primary key: provider + day)
```

- **Save first, merge later:** sightings are saved with a null `business_id`, so a crash before merge doesn't lose results or repeat paid calls. Merge sets `business_id`.
- **Allowed values:** status and source columns have CHECK constraints built from the same const arrays as the TypeScript types.
- **Indexes:**
  - `businesses`: unique on `maps_place_id`; plain on `website_domain`, `instagram`, `phone`, and `city` + `status`.
  - `sightings`: `business_id`, plus a partial index for unmerged rows (`WHERE business_id IS NULL`).
  - `pages`: unique on `business_id` + `url`.
  - `qualifications`: `business_id`.
- **No cascading deletes:** foreign keys block deletes, so a merge must move sightings, pages, qualifications and (from 1.5) evidence and notes to the surviving business before deleting the other one.

Planned for Phase 1.5:

```
evidence        business_id, field, value, source, source_url, quote, confidence, created_at
overrides       business_id, field, value, edited_by_user_at   (locked against later runs)
jobs            type, location, params, status(queued|running|done|failed|cancelled), progress, error
businesses      + physical_store (yes|no|unknown), physical_store_confidence (high|medium|low),
                + system_decision (lead|review|excluded), set by the scraper, recalculated each run
                + crm_status (to_prospect|contacted|followed_up|qualified|in_discussion|offer_sent|
                  to_recontact|lost|invalid), set only by the user
                + last_contacted, next_follow_up
                + google_place_id, google_maps_url (replaces maps_place_id)
                - status (replaced by system_decision + crm_status)
runs            city → location
```

- **Google Maps storage (main spec §7.1):** only `google_place_id` and `google_maps_url` are stored permanently, plus the verification result as evidence. Lat/lng may be cached for 30 days at most. Other details are fetched live; address and phone in the CRM come from the shop's website or the user.
- **Not in the schema yet, though tech spec §5.4 lists them as built:** a content hash on `pages`, and input hash + prompt version + per-signal rows on `qualifications`.

Dedupe = Places `place_id` → website domain → IG handle → normalized phone → normalized name + locality (fuzzy, e.g. `fastest-levenshtein`).

### Qualification and scoring (main spec §8, §12–17)

- One LLM call per business fills the market's signals: YES / NO / UNKNOWN, a quote and a source URL. A signal without a quote counts as UNKNOWN.
- The score (0–100) comes from config weights. YES counts in full, UNKNOWN half, NO zero, and each score stores its breakdown.
- Physical store is a gate on priority, not points. "Not found on Maps" means UNKNOWN, never NO.
- Priority: TOP / HIGH / MEDIUM / LOW / EXCLUDE. Anazvara Fit is a band derived from the score.

## 4. Phased plan (each item ≈ one session)

Each phase is reviewed and committed before the next one starts.

### Phase 0 — Freeze & define "good" (partly done)
- [x] Move the Python version to `legacy/`.
- [ ] Tag the Python repo `python-v0` (no tag exists yet).
- [ ] Create `eval/golden.json`: reference stockists and "looks close but no" examples per market, each with location and expected outcome.
- [x] Define targets per market (main spec §33): **≥ 80% of reference stockists found and ending TOP or HIGH**, **≥ 60% of the top 50 confirmed by manual review**, and **no reference "no" example ending TOP**.

### Phase 1 — Skeleton & storage (in progress)
- [x] Tooling: pnpm, TS strict, ESLint, Vitest (`942ce6d`).
- [x] Database: Drizzle schema + migrations for the tables above (`2d2591d`).
- [x] Config (`zod`, `src/config/`): `config/app.json` (providers, budgets, thresholds), `config/places.json` (India, Goa as a region, and 9 cities ported from `geography.py`), `config/markets.json` (India) and `config/anazvara-profile.json` (placeholders).
- [x] `Provider` interface: `search(query, ctx) → Sighting[]` + budget guard (`src/providers/`). The registry is empty until Phase 2.
- [x] CLI skeleton (`src/cli.ts`, `src/cli/`): `find`, `places`, `config`, `budget`, `db migrate`. `find` resolves the place and records a run; discovery comes in Phase 2.
- [ ] Wrap-up: review Phase 1 against this plan.

### Phase 1.5 — Workspace & schema (next, only after Phase 1)
- [ ] Move to a pnpm workspace: `apps/scraper`, `packages/database`, `packages/shared`, `packages/scoring` (`apps/web` comes in Phase 7).
- [ ] Add the evidence, overrides and jobs tables and the `businesses` fields from section 3.

### Phase 2 — High-yield sources first
- [ ] **Overpass provider**: clothes/boutique/fashion shops in the location's bbox → sightings.
- [ ] **Google Places provider**: Text Search per neighbourhood × store-type wording, field mask limited to what you need. Hard daily cap. Applies the §7.1 storage rule.
- [ ] Matching + merge/dedupe service → `businesses`.
- [ ] Run Goa: **check golden-set recall now.**

### Phase 3 — Web & semantic discovery
- [ ] SearXNG provider: market language, explicit engines, query families + neighbourhood rotation.
- [ ] Exa and Tavily providers, with location settings taken from the places catalog.
- [ ] Result-type classifier (port `classification.py` logic): website / social / directory / article / marketplace / maps.
- [ ] **Guide expansion**: for article/directory pages, fetch text → LLM extracts `[{name, locality, hints}]` → sightings with `source=guide`. *(This recovers the 236 lost shops.)*

### Phase 4 — Enrich & verify
- [ ] Resolver: for businesses missing website/IG/address, query Places → Exa → SearXNG with `"<name>" <locality> <location>` and attach.
- [ ] Light pass for every candidate: homepage + up to 3 same-site pages (contact/about/stores/stockists), JSON-LD, `og:site_name`, emails, phones, IG/FB/WhatsApp, address. Respect robots, 15 s timeout, 1 req/s per host, 1 MiB cap.
- [ ] Physical-store verification: Maps + website evidence → `physical_store` + confidence, with evidence rows.
- [ ] Full pass once `physical_store` = YES: brands, products, typical prices, contacts.
- [ ] Shopify trick: `/pages/contact`, `/pages/about`, `/pages/stores`, and `/collections/vendors` or `/products.json` vendor field → **multi-brand evidence for free**.

### Phase 5 — Qualify & score
- [ ] Ollama client with JSON-schema `format`, temperature 0, one retry on schema failure, prompt + schema versioned.
- [ ] Input pack ≤ 3k tokens: Maps verification result + JSON-LD + page excerpts + guide mentions + Anazvara profile + market signals.
- [ ] Rules in `packages/scoring` + config thresholds → score, fit, priority, `system_decision`.
- [ ] Cache by input hash + model + prompt version, so reruns don't call the LLM again.

### Phase 6 — The product command
- [ ] `pnpm cli find --location Goa --new 50`: runs stages until 50 *new* LEAD+REVIEW businesses exist that weren't in previous runs.
- [ ] `pnpm cli export --location Goa` → CRM-ready CSV.
- [ ] `pnpm cli eval` → recall + precision report per market.
- [ ] Remaining commands from tech spec §5.2 as needed (`budget`, `config`, `places`, `db migrate`, `worker`).

### Phase 7 — Next.js CRM
- [ ] `apps/web`: prospect list, detail with evidence, statuses, notes, dashboard, jobs (worker in `apps/scraper`).

### Ongoing
- Tune on eval only (never hard-code shop names into queries). Add places and markets through config.

## 5. Repo layout

Today: a single package (`src/`, `drizzle/`, `test/`). Target from Phase 1.5:

```
apps/
  scraper/src/               # cli.ts, providers/ (+ budget.ts), discover/, resolve/, enrich/, qualify/, export/
  web/                       # Next.js CRM (Phase 7)
packages/
  database/                  # Drizzle schema, migrations, client
  shared/                    # config schemas, types, places catalog loader
  scoring/                   # score, fit, priority (pure functions)
config/                      # app.json, places.json, markets.json, anazvara-profile.json
eval/golden.json
legacy/ (python, read-only)
```

## 6. Risks & guardrails

- **Google Places billing**: needs a billing account. Set per-API daily quotas (e.g. 300/day) on day one.
- **Places ToS**: see the storage rule in section 3. Check the current terms before production use.
- **Personal data** (main spec §34): a contact person is personal data under DPDP and GDPR. Collect only publicly presented business contacts, record their source, and honour a do-not-contact flag.
- **Local LLM speed**: ~5–15 s per qualification on a 9B model on a laptop. 300 shops ≈ 1 hour, so run it in the background and cache results.
- **Overfitting to Goa**: keep the golden set multi-city and multi-market.

## 7. Open decisions

- First European country to target, and its query language(s) (main spec §35).
- Do LEAD prospects enter `to_prospect` automatically, or only after review? (main spec §35)
- How `system_decision` is derived from priority and score: the specs don't define it, and `find --new` depends on it.
- Places sightings: `sightings.raw_json` keeps full raw results and tech spec §5.3 lists name, address and phone on `Sighting`, which conflicts with the §7.1 storage rule. Decide what Places sightings keep before Phase 2.
