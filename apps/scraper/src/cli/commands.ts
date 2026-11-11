import { existsSync } from "node:fs";
import { relative } from "node:path";
import { finishRun, openDb, startRun, type Db } from "@anazvara/database";
import {
  CONFIG_DIR,
  expandPlace,
  findPlace,
  loadConfig,
  PROVIDER_NAMES,
  providerStatuses,
  resolveFromRoot,
  type Place,
  type PlaceType,
  type ProviderName,
  type ProviderStatus,
  type SearchArea,
} from "@anazvara/shared";
import { BudgetGuard } from "../providers/budget.js";
import { buildProviders, PROVIDER_FACTORIES, type Env, type ProviderFactories } from "../providers/registry.js";
import { UserError } from "./errors.js";
import { formatTable, type Row } from "./table.js";

export const DEFAULT_DATABASE_PATH = "data/stockists.db";
export const DEFAULT_NEW_COUNT = 50;

export interface CliContext {
  env: Env;
  out: (line: string) => void;
  /** Defaults to the repo's config/ directory. */
  configDir?: string;
  factories?: ProviderFactories;
}

// ---- Argument parsing -------------------------------------------------------

/** DATABASE_PATH from the environment, resolved against the repo root (not the working directory). */
export function databasePath(env: Env): string {
  const path = env.DATABASE_PATH?.trim() || DEFAULT_DATABASE_PATH;
  return path === ":memory:" ? path : resolveFromRoot(path);
}

export function parseNewCount(value: string): number {
  const count = Number(value);
  if (!/^\d+$/.test(value.trim()) || !Number.isSafeInteger(count) || count < 1) {
    throw new UserError(`--new must be a whole number of at least 1, got "${value}"`);
  }
  return count;
}

/** A comma-separated provider list. Undefined means all providers. */
export function parseSources(value: string | undefined): ProviderName[] | undefined {
  if (value === undefined) return undefined;
  const names = [...new Set(value.split(",").map((name) => name.trim().toLowerCase()).filter(Boolean))];
  if (names.length === 0) {
    throw new UserError(`--sources needs at least one provider. Valid names: ${PROVIDER_NAMES.join(", ")}`);
  }
  const unknown = names.filter((name) => !(PROVIDER_NAMES as readonly string[]).includes(name));
  if (unknown.length > 0) {
    const list = unknown.map((name) => `"${name}"`).join(", ");
    throw new UserError(`Unknown provider ${list} in --sources. Valid names: ${PROVIDER_NAMES.join(", ")}`);
  }
  return names as ProviderName[];
}

export function resolvePlace(places: readonly Place[], query: string): Place {
  const place = findPlace(places, query);
  if (place === undefined) {
    const keys = places.map((known) => known.key).join(", ");
    throw new UserError(`Unknown place "${query}". Known places: ${keys}`);
  }
  return place;
}

// ---- Output rows ------------------------------------------------------------

const PLURALS: Record<SearchArea["type"], string> = {
  neighbourhood: "neighbourhoods",
  city: "cities",
  region: "regions",
  country: "countries",
};

/** "25 (25 neighbourhoods)" or "10 (9 cities, 1 region)". */
export function describeAreas(areas: readonly SearchArea[]): string {
  const counts = new Map<SearchArea["type"], number>();
  for (const { type } of areas) counts.set(type, (counts.get(type) ?? 0) + 1);
  const parts = [...counts].map(([type, count]) => `${count} ${count === 1 ? type : PLURALS[type]}`);
  return `${areas.length} (${parts.join(", ")})`;
}

export function placeRows(places: readonly Place[], type?: PlaceType): Row[] {
  return places
    .filter((place) => type === undefined || place.type === type)
    .map((place) => [
      place.key,
      place.name,
      place.type,
      place.country,
      String(place.neighbourhoods.length),
      place.aliases.join(", ") || "-",
    ]);
}

export function providerRows(statuses: readonly ProviderStatus[]): Row[] {
  return statuses.map(({ name, active, reason }) => [name, active ? "yes" : "no", active ? "-" : reason]);
}

export function budgetRows(guard: BudgetGuard): Row[] {
  return PROVIDER_NAMES.map((name) => {
    const usage = guard.usage(name);
    return [
      name,
      `${usage.dailyCalls}/${usage.dailyLimit}`,
      `${usage.monthlyCalls}/${usage.monthlyLimit}`,
      usage.monthlyCostUsd.toFixed(2),
    ];
  });
}

// ---- Commands ---------------------------------------------------------------

function withDb<T>(ctx: CliContext, use: (db: Db) => T): T {
  const db = openDb(databasePath(ctx.env));
  try {
    return use(db);
  } finally {
    db.$client.close();
  }
}

function printTable(ctx: CliContext, header: Row, rows: readonly Row[]): void {
  for (const line of formatTable(header, rows)) ctx.out(line);
}

export interface FindOptions {
  location: string;
  new: string;
  sources?: string;
}

export function runFind(ctx: CliContext, options: FindOptions): void {
  // Check everything the user typed before touching the database.
  const target = parseNewCount(options.new);
  const only = parseSources(options.sources);
  const config = loadConfig(ctx.configDir);
  const place = resolvePlace(config.places, options.location);
  const areas = expandPlace(config.places, place);

  withDb(ctx, (db) => {
    const run = startRun(db, {
      city: place.key,
      params: { command: "find", location: options.location, placeKey: place.key, new: target, sources: only ?? "all" },
    });
    ctx.out(`Run #${run.id}`);
    ctx.out(`Place: ${place.name} (${place.type}, ${place.country}) [${place.key}]`);
    ctx.out(`Searchable areas: ${describeAreas(areas)}`);
    ctx.out(`Target: ${target} new boutiques`);

    const { providers, skipped } = buildProviders(config, ctx.env, db, ctx.factories ?? PROVIDER_FACTORIES, only);
    ctx.out(`Active providers: ${providers.map((provider) => provider.name).join(", ") || "none"}`);
    for (const { name, reason } of skipped) ctx.out(`  skipped ${name}: ${reason}`);
    ctx.out("Discovery providers arrive in Phase 2: nothing was searched.");

    finishRun(db, run.id, {
      areas: areas.length,
      providers: providers.map((provider) => provider.name),
      skipped,
      sightings: 0,
      newBusinesses: 0,
    });
    ctx.out(`Run #${run.id} finished: 0 sightings, 0 new businesses.`);
  });
}

export function runPlaces(ctx: CliContext, options: { type?: PlaceType }): void {
  const { places } = loadConfig(ctx.configDir);
  printTable(ctx, ["key", "name", "type", "country", "neighbourhoods", "aliases"], placeRows(places, options.type));
}

export function runConfig(ctx: CliContext): void {
  const config = loadConfig(ctx.configDir);
  const dir = relative(process.cwd(), ctx.configDir ?? CONFIG_DIR) || ".";
  const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;
  ctx.out(
    `Config OK (${dir}/): app.json, places.json (${count(config.places.length, "place")}), ` +
      `markets.json (${count(config.markets.length, "market")}), anazvara-profile.json`,
  );
  ctx.out(`Database: ${databasePath(ctx.env)}`);
  ctx.out("");
  printTable(ctx, ["provider", "active", "reason"], providerRows(providerStatuses(config.app, ctx.env)));
}

export function runBudget(ctx: CliContext): void {
  const config = loadConfig(ctx.configDir);
  withDb(ctx, (db) => {
    const guard = new BudgetGuard(db, config.app.providers);
    printTable(ctx, ["provider", "today", "month", "month USD"], budgetRows(guard));
  });
}

export function runMigrate(ctx: CliContext): void {
  const path = databasePath(ctx.env);
  const existed = path === ":memory:" || existsSync(path);
  withDb(ctx, (db) => {
    const { count } = db.$client.prepare("select count(*) as count from __drizzle_migrations").get() as {
      count: number;
    };
    ctx.out(`${existed ? "Database" : "Created database"}: ${path}`);
    ctx.out(`Migrations applied: ${count} (up to date)`);
  });
}
