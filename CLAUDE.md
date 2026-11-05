# Anazvara Stockist Finder

Finds physical multi-brand / concept / designer boutiques in India that could stock Anazvara (contemporary Indian womenswear). The full plan is in @PLAN.md. Work one phase at a time and don't start the next phase unless asked.

## Stack
- Node 22, TypeScript **6** strict (not 7: typescript-eslint doesn't support TS 7 yet), pnpm, ESM, `tsx` for running TS.
- SQLite via `better-sqlite3` + Drizzle ORM (`drizzle-kit` for migrations in `drizzle/`).
- `zod` v4 for config and all LLM output. `commander` for the CLI. `vitest` for tests.
- pnpm 12: native builds are allowed via allowBuilds in pnpm-workspace.yaml (better-sqlite3, esbuild). onlyBuiltDependencies is ignored, so don't add it.

## Commands
- `pnpm check`: typecheck + lint + tests. Run it after every change and fix failures before saying a task is done.
- `pnpm db:generate --name <name>` after editing `src/db/schema.ts`. Never hand-edit files in `drizzle/`.
- `pnpm cli <command>` runs the CLI.
- `pnpm cli find --city <city>` finds boutiques in a city.

## Rules
- `legacy/` is the old Python version. Read it for ideas (`legacy/src/geography.py`, `url_normalization.py`, `deduplication.py`, `search_queries.py`). Never modify it or port it line by line.
- Cost: every paid API call goes through `src/providers/budget.ts`. Paid providers default to `enabled: false`. Never add a paid service without a config flag.
- Don't reject early. Providers only collect `Sighting[]`. Only the qualify + score stages decide lead/review/rejected.
- Never hard-code shop names in queries or logic. Reference shops live only in `eval/golden.json`.
- Respect robots.txt, 1 request/second per host, 15 s timeout, 1 MiB cap. No scraping of Instagram or Google Maps HTML; official APIs only.
- Thresholds and weights go in `config/app.json`, not in code.
- Small pure functions. Each new module gets a vitest test. Network calls in tests use recorded fixtures in `test/fixtures/`.
- Commit messages: short imperative subject, body explaining why.
