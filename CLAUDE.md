# Anazvara Stockist Finder

Finds physical multi-brand / concept / designer boutiques in India and other markets (Europe) that could stock Anazvara (contemporary Indian womenswear). The phase plan and repo status are in @PLAN.md. Work one phase at a time and don't start the next phase unless asked.

## Product source of truth (Notion, spec v1.1)
- [Main spec](https://app.notion.com/p/3e9be93e879780f7a5f4f385496c7bbc)
- [Product specification](https://app.notion.com/p/3e9be93e87978019b53fe433880f0c90)
- [Technical spec](https://app.notion.com/p/3e9be93e879780feb6cad8e2341911ae)

## Stack
- Node 22, TypeScript **6** strict (not 7: typescript-eslint doesn't support TS 7 yet), pnpm, ESM, `tsx` for running TS.
- SQLite via `better-sqlite3` + Drizzle ORM (`drizzle-kit` for migrations in `packages/database/drizzle/`).
- `zod` v4 for config and all LLM output. `commander` for the CLI. `vitest` for tests.
- pnpm 12: native builds are allowed via allowBuilds in pnpm-workspace.yaml (better-sqlite3, esbuild). onlyBuiltDependencies is ignored, so don't add it.
- Layout: pnpm workspace. `apps/scraper` (CLI, providers, later the pipeline stages), `packages/database` (`@anazvara/database`: schema, client, runs, migrations), `packages/shared` (`@anazvara/shared`: config schemas and loader, places, provider statuses, `REPO_ROOT`), `packages/scoring` (`@anazvara/scoring`, Phase 5), `apps/web` later. Each has `src/` and `test/`. `config/`, `data/`, `eval/`, `docs/`, `legacy/`, `searxng/` stay at the root.
- Packages are imported by name (`@anazvara/shared`), never by relative path across packages. Their `exports` point at TS source, so there is no build step; one root `tsconfig.json`, `vitest.config.ts` and `eslint.config.js` cover the whole repo.
- Paths to `config/`, `.env` and `DATABASE_PATH` resolve from the repo root (`REPO_ROOT` / `resolveFromRoot` in `@anazvara/shared`), never from the working directory.

## Commands
- All commands run from the repo root.
- `pnpm check`: typecheck + lint + tests. Run it after every change and fix failures before saying a task is done.
- `pnpm db:generate --name <name>` after editing `packages/database/src/schema.ts`. Never hand-edit files in `packages/database/drizzle/`.
- The CLI is always `pnpm cli <command>`, never `pnpm <command>`.
- `pnpm cli find --location <place>` finds boutiques in a city, region or country (no providers until Phase 2). Other commands: `places`, `config`, `budget`, `db migrate`; see README.

## Rules
- `legacy/` is the old Python version. Read it for ideas (`legacy/src/geography.py`, `url_normalization.py`, `deduplication.py`, `search_queries.py`). Never modify it or port it line by line.
- Cost: every paid API call goes through `apps/scraper/src/providers/budget.ts`. Paid providers default to `enabled: false`. Never add a paid service without a config flag.
- Don't reject early. Providers only collect `Sighting[]`. Only the qualify + score stages decide `system_decision` (lead/review/excluded).
- The scraper never writes `crm_status` or other CRM fields; only the user sets them. Fields the user edited are never overwritten by a later run.
- Never hard-code shop names in queries or logic. Reference shops live only in `eval/golden.json`.
- Respect robots.txt, 1 request/second per host, 15 s timeout, 1 MiB cap. No scraping of Instagram or Google Maps HTML; official APIs only.
- From Google Maps, store only `google_place_id` and the Maps URL permanently (main spec §7.1).
- Thresholds and weights go in `config/app.json`, locations in `config/places.json`. Signals, query language, currency and price band are per market in config. None of these go in code.
- Small pure functions. Each new module gets a vitest test. Network calls in tests use recorded fixtures in the package's `test/fixtures/`.
- Commit messages: short imperative subject, body explaining why.
