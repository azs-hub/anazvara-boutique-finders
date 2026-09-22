# Anazvara Boutique Scraper

Free Python application that discovers clothing and fashion boutiques city by city, stores historical results in SQLite, and exports an Excel file.

The application does **not** use AI/LLM APIs and does **not** consume OpenAI tokens.

Current discovery flow:

```
SearXNG discovery
    → candidate normalization
    → candidate classification
    → public page fetch + evidence extraction
    → optional depth-1 same-domain enrichment (Contact / Store / About)
    → deterministic BusinessCandidate identification
    → later: historical SQLite / Excel
```

Business identification is conservative and rule-based. Weak evidence stays `UNKNOWN`. Directory/article pages yield linked businesses; those websites are not fetched in this step.

## Requirements

- macOS or Linux
- Python 3.11
- Docker and Docker Compose (for the local SearXNG instance)
- A project virtual environment at `.venv` (already created for local development)

## Setup

From the project root:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

On some systems the interpreter is `python3` rather than `python`. This project uses `.venv/bin/python`.

## Discovery (search providers)

The discovery layer is split so the rest of the application does not depend on one search engine:

- `src/search_provider.py` — `SearchProvider.search(query, page)` and `SearchResult`
- `src/searxng_provider.py` — SearXNG JSON API implementation
- `src/discovery.py` — later city/quantity boutique discovery (still a stub)

Search results include title, URL, snippet, and source/engine name.

Candidate discovery (`src/candidates.py`) then:

- Normalizes URLs (fragments, `www.`, tracking parameters, trailing slashes; **paths are kept**)
- Extracts a comparable host/domain (`www.example.com` ≡ `example.com`)
- Classifies each hit with URL heuristics only (not AI): `WEBSITE`, `SOCIAL`, `DIRECTORY`, `ARTICLE`, `VIDEO`, `UNKNOWN`
- Deduplicates **WEBSITE** hits that share a domain, keeping the most useful URL (usually the homepage)

Directory and article results are **kept**. They are not discarded; a later step can fetch those pages and extract boutique links (`expand_from_page`). Historical SQLite exclusion is also later.

### Public page fetch and evidence

`src/fetcher.py` performs bounded HTTP GETs with `requests` (timeout 15s, 0 retries by default, 1s delay between page requests, 1 MiB response cap). It retrieves and caches `robots.txt` per origin before allowed page requests. HTTP errors, timeouts, and connection failures are recorded, not raised.

`src/content_extraction.py` uses BeautifulSoup to collect `PageEvidence` (title, description, visible text, headings, absolute links) and `BusinessSignals` (emails, phones, social/WhatsApp URLs, address-like snippets, city mentions). These are **signals only** — not boutique identification.

Limits: 50 000 characters of visible text, 200 links, 40 headings, 30 items per signal list.

```bash
source .venv/bin/activate
python src/fetch_test.py https://example.com
python src/fetch_test.py --from-search "women's fashion boutique Mumbai" --limit 5
```

The `--from-search` form fetches a small mixed sample (not the full result list). SOCIAL/VIDEO pages are attempted once; if the site blocks the request, the candidate URL is kept as evidence.

### Business identification (deterministic)

`src/business_candidates.py` turns `PageEvidence` + `BusinessSignals` into `BusinessCandidate` records. It does not use AI. A directory or article page can produce many candidates; a website page usually produces one (the owner), unless the title is clearly a roundup list.

```bash
source .venv/bin/activate
python src/identify_test.py "women's fashion boutique Mumbai"
```

This fetches a small mixed sample (2 websites, 2 directories, 1 social, 1 video when available) and prints identified businesses. It does not crawl the extracted websites.

### Identification benchmark

Measure the current deterministic baseline on a larger SearXNG result set (no crawling of extracted links):

```bash
source .venv/bin/activate
python src/benchmark.py "women's fashion boutique Mumbai" --limit 50
python src/benchmark.py "women's fashion boutique Mumbai" --limit 100
```

Writes `output/benchmark_YYYYMMDD_HHMMSS.json` (not committed). This homepage-only run is the Step 6.5 baseline.

### STEP 8 — Enrichment impact benchmark

Compare the same query with controlled enrichment against the saved Step 6.5 homepage-only JSON. Measurement only — it does not tune heuristics or add AI.

```bash
source .venv/bin/activate
python src/enrichment_benchmark.py "women's fashion boutique Mumbai" --limit 50
python src/enrichment_benchmark.py "women's fashion boutique Mumbai" --limit 100
```

Writes `output/benchmark_enriched_YYYYMMDD_HHMMSS.json` (not committed). Count increases are not treated as automatically better.

### STEP 7 — Controlled enrichment

For WEBSITE candidates only, the homepage is fetched, then a small set of **same-domain** internal pages (Contact, Stores/Locations, About). Evidence is combined before identification.

```
SearXNG result
    → homepage
    → internal links + sitemap (discovery only)
    → relevant page selection
    → maximum 3 enrichment pages
    → combined evidence
    → deterministic identification
```

Rules:

- Depth **1** only (homepage links plus selected sitemap URLs; no second-level crawl)
- Maximum **3** internal pages per candidate (4 HTML fetches including the homepage)
- Sitemap is a **URL discovery source only** — product/category URLs are filtered out
- Child sitemap indexes: at most **1** extra level and **5** child sitemaps
- Same registrable site only (`example.com` ≡ `www.example.com`; no Instagram/Facebook/marketplaces)
- Existing fetcher: timeout, User-Agent, `robots.txt`, HTML/XML limits, no extra retries
- No recursive website crawl, pagination, or query-parameter exploration
- No AI, no database writes, no paid APIs

```bash
source .venv/bin/activate
python -m src.enrichment_test
```

Uses query `women's fashion boutique Mumbai` and up to 5 WEBSITE candidates.

### Local SearXNG (Docker Compose)

SearXNG currently runs **local-only** on this Mac. The published port is bound to `127.0.0.1`, not the public internet. Do not expose it publicly.

Local URL: [http://localhost:8888](http://localhost:8888)

Create the local secret file once (not committed):

```bash
cp searxng/.env.example searxng/.env
python3 -c "import secrets; print(secrets.token_hex(32))"
# paste the printed value into searxng/.env as SEARXNG_SECRET=...
cp .env.example .env
```

`.env` (project root) must contain:

```
SEARXNG_URL=http://localhost:8888
```

Start:

```bash
docker compose -f searxng/docker-compose.yml up -d
```

Check status:

```bash
docker compose -f searxng/docker-compose.yml ps
```

Stop:

```bash
docker compose -f searxng/docker-compose.yml down
```

`down` stops the containers. Named Docker volumes (`core-data`, `valkey-data`) keep cache/data unless you also pass `-v`.

### Run the search test

With SearXNG running:

```bash
source .venv/bin/activate
python src/search_test.py "women's fashion boutique Mumbai"
```

This sends the query to the local SearXNG `/search` JSON endpoint and prints titles, URLs, and snippets. It does not fetch or scrape the result websites.

### Run the candidate discovery test

With SearXNG running:

```bash
source .venv/bin/activate
python src/candidate_test.py "women's fashion boutique Mumbai"
```

This classifies and deduplicates the search hits. It does not scrape websites or follow directory pages.

### Unit tests (no network)

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

## Run the SQLite / Excel smoke test

```bash
source .venv/bin/activate
python src/main.py
```

Expected result:

- Creates `data/boutiques.db` if it does not exist
- Inserts one sample boutique (skipped on later runs if already stored)
- Rejects a duplicate insert
- Writes `output/boutiques.xlsx`

## Project layout

```
anazvara-boutique-scraper/
├── .venv/
├── data/
│   └── boutiques.db          # created at runtime; not committed
├── output/
│   └── boutiques.xlsx        # created at runtime; not committed
├── src/
│   ├── main.py
│   ├── discovery.py          # city/quantity workflow — not implemented yet
│   ├── search_provider.py    # search interface
│   ├── searxng_provider.py   # SearXNG JSON provider
│   ├── search_test.py        # CLI search test
│   ├── url_normalization.py
│   ├── classification.py     # heuristic result types (not AI)
│   ├── candidates.py         # Candidate model + in-search dedup
│   ├── candidate_test.py     # CLI candidate discovery test
│   ├── fetcher.py
│   ├── content_extraction.py
│   ├── page_inspection.py
│   ├── fetch_test.py
│   ├── business_candidates.py
│   ├── identify_test.py
│   ├── enrichment.py
│   ├── sitemap.py
│   ├── enrichment_test.py
│   ├── benchmark.py
│   ├── enrichment_benchmark.py
│   ├── scraper.py            # boutique record model; identification later
│   ├── database.py
│   ├── deduplication.py
│   └── excel_export.py
├── searxng/
│   ├── docker-compose.yml
│   ├── .env.example
│   └── core-config/
│       └── settings.yml      # JSON output enabled; secrets stay in searxng/.env
├── tests/
│   ├── test_searxng_parse.py
│   ├── test_candidate_discovery.py
│   ├── test_fetcher.py
│   ├── test_content_extraction.py
│   ├── test_business_candidates.py
│   ├── test_deduplication.py
│   ├── test_enrichment.py
│   └── test_enrichment_benchmark.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Planned user input (later)

- City (example: Mumbai)
- Number of **new** boutiques to find (example: 50)

Later runs for the same city must exclude boutiques already stored in SQLite. The goal is “find N new boutiques,” not “return N search results.”

## Not in this version

- Boutique identification beyond the deterministic baseline (plus optional depth-1 enrichment)
- Writing extracted businesses to SQLite or Excel
- Recursive crawling or fetching websites extracted from directories
- Playwright
- AI / LLM APIs
- Paid search or scraping APIs
- Google Maps scraping
- Proxies or CAPTCHA-solving services
- GUI
- Final city + quantity workflow
