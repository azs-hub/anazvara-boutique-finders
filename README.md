# Anazvara Boutique Scraper

Free Python application that discovers clothing and fashion boutiques city by city, stores historical results in SQLite, and exports an Excel file.

The application does **not** use AI/LLM APIs and does **not** consume OpenAI tokens.

Current discovery flow:

```
SearXNG discovery
    → candidate normalization
    → candidate classification
    → future website scraping
```

This step does **not** scrape boutique websites, extract emails/phones/Instagram, expand directory pages, or run the final city/quantity workflow.

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

Website scraping is not implemented yet.

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
python -m unittest tests.test_searxng_parse tests.test_candidate_discovery
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
│   ├── scraper.py            # stub — not implemented yet
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
│   └── test_candidate_discovery.py
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

- Boutique website scraping (emails, phones, Instagram, addresses)
- Playwright
- AI / LLM APIs
- Paid search or scraping APIs
- Google Maps scraping
- Proxies or CAPTCHA-solving services
- GUI
- Final city + quantity workflow
