# Anazvara Boutique Scraper

Free Python application that discovers clothing and fashion boutiques city by city, stores historical results in SQLite, and exports an Excel file.

The application does **not** use AI/LLM APIs and does **not** consume OpenAI tokens.

This step adds an isolated SearXNG search-provider test. It does **not** scrape boutique websites, extract emails/phones/Instagram, or run the final city/quantity workflow.

## Requirements

- macOS or Linux
- Python 3.11
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

Search results include title, URL, snippet, and source/engine name. Website scraping is a later step.

### Configure SearXNG

No public SearXNG instance is hard-coded. Set the base URL with an environment variable:

```bash
cp .env.example .env
```

`.env.example` contains:

```
SEARXNG_URL=http://localhost:8888
```

Point `SEARXNG_URL` at your own SearXNG instance. We will later run a private SearXNG instance locally on macOS and on a Linux DigitalOcean server. Do not commit `.env`.

### Run the search test

```bash
source .venv/bin/activate
python src/search_test.py "women's fashion boutique Mumbai"
```

This sends the query to the configured SearXNG `/search` JSON endpoint and prints titles, URLs, and snippets. It does not fetch or scrape the result websites.

### Parse unit test (no network)

```bash
source .venv/bin/activate
python -m unittest tests.test_searxng_parse
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
│   ├── scraper.py            # stub — not implemented yet
│   ├── database.py
│   ├── deduplication.py
│   └── excel_export.py
├── tests/
│   └── test_searxng_parse.py
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
