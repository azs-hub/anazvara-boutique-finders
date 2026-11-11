# Anazvara Stockist Finder

Finds physical multi-brand, concept and designer boutiques in India and Europe that could stock Anazvara. See `PLAN.md` for the phase plan.

## Setup

```sh
pnpm install
cp .env.example .env   # every key is optional until its provider is enabled
pnpm cli db migrate
```

## Commands

Run every command as `pnpm cli <command>`. `.env` is loaded at startup; variables already set in the shell take precedence.

| Command | What it does |
|---|---|
| `find --location <place> [--new <n>] [--sources <list>]` | Finds boutiques in a city, region or country. `<place>` is a key, name or alias from `config/places.json` (case-insensitive). `--new` is the number of new boutiques to aim for (default 50). `--sources` is a comma-separated list of providers (`osm`, `searxng`, `places`, `exa`, `tavily`) and defaults to all. Records a run in the database. Discovery providers arrive in Phase 2, so for now it prints the place, its searchable areas and why each provider was skipped. |
| `places [--type city\|region\|country]` | Lists the places: key, name, type, country, number of neighbourhoods, aliases. |
| `config` | Validates all config files and shows the database path and which providers are active (and why not). |
| `budget` | Shows API usage per provider: today and this month against their limits, and this month's cost in USD. |
| `db migrate` | Creates the database (`DATABASE_PATH`, default `data/stockists.db`) and applies migrations. |

An unknown place, an unknown provider or invalid config prints the error and exits with code 1.

## Development

- `pnpm check`: typecheck, lint and tests.
- `pnpm db:generate --name <name>` after editing `src/db/schema.ts`.
