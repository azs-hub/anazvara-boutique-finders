# Anazvara Boutique Scraper

Free Python application that discovers clothing and fashion boutiques city by city, stores historical results in SQLite, and exports an Excel file.

This first increment only sets up the project, SQLite, and a sample Excel export. Web discovery and website scraping are not implemented yet.

The application does **not** use AI/LLM APIs and does **not** consume OpenAI tokens.

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

## Run the current smoke test

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
│   ├── discovery.py          # stub — not implemented yet
│   ├── scraper.py            # stub — not implemented yet
│   ├── database.py
│   ├── deduplication.py
│   └── excel_export.py
├── .gitignore
├── requirements.txt
└── README.md
```

## Planned user input (later)

- City (example: Mumbai)
- Number of **new** boutiques to find (example: 50)

Later runs for the same city must exclude boutiques already stored in SQLite. The goal is “find N new boutiques,” not “return N search results.”

## Not in this version

- Web discovery / search providers
- Website scraping
- Playwright
- AI / LLM APIs
- GUI
