import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { loadConfig, findPlace, type Place } from "@anazvara/shared";
import { openDb, sightings, startRun, type Db } from "@anazvara/database";
import { discover, queriesFor } from "../../src/discover/discover.js";
import { BudgetGuard, withBudget } from "../../src/providers/budget.js";
import { fakeProvider } from "../providers/fake.js";

const config = loadConfig();
const place = (key: string): Place => findPlace(config.places, key)!;

let db: Db;
let runId: number;

beforeEach(() => {
  db = openDb(":memory:");
  runId = startRun(db, { command: "find", city: "goa" }).id;
});

afterEach(() => {
  db.$client.close();
});

const guard = (limits = config.app.providers) => new BudgetGuard(db, limits);

describe("queriesFor", () => {
  test("an area provider gets one query per area with a bbox", () => {
    expect(queriesFor({ supports: ["area"] }, config.places, place("goa")).map((query) => query.area.place.key)).toEqual([
      "goa",
    ]);
    expect(queriesFor({ supports: ["area"] }, config.places, place("india"))).toHaveLength(config.places.length - 1);
  });

  test("a text-only provider gets none until Phase 3", () => {
    expect(queriesFor({ supports: ["text"] }, config.places, place("goa"))).toEqual([]);
  });
});

describe("discover", () => {
  test("runs every query, saves the sightings and counts calls", async () => {
    const osm = withBudget(fakeProvider("osm", { supports: ["area"] }), guard());

    const { stats, inserted } = await discover(db, runId, [osm], config.places, place("india"));

    const queries = config.places.length - 1;
    expect(stats).toEqual([
      { provider: "osm", queries, queriesDone: queries, calls: queries, found: queries, inserted: queries, alreadySeen: 0, unnamed: 0 },
    ]);
    expect(inserted).toHaveLength(queries);
    expect(db.select().from(sightings).all()).toHaveLength(queries);
  });

  test("a failing provider is reported and the next one still runs", async () => {
    const broken = withBudget(fakeProvider("osm", { fail: true }), guard());
    const working = withBudget(fakeProvider("searxng", { supports: ["area"] }), guard());

    const { stats } = await discover(db, runId, [broken, working], config.places, place("india"));

    expect(stats[0]).toMatchObject({ provider: "osm", queriesDone: 0, calls: 1, error: "upstream failed" });
    expect(stats[1]).toMatchObject({ provider: "searxng", queriesDone: config.places.length - 1 });
  });

  test("the budget stops a provider cleanly and keeps what it found", async () => {
    const limits = structuredClone(config.app.providers);
    limits.osm.dailyLimit = 2;
    const osm = withBudget(fakeProvider("osm", { supports: ["area"] }), guard(limits));

    const { stats } = await discover(db, runId, [osm], config.places, place("india"));

    expect(stats[0]).toMatchObject({ queriesDone: 2, calls: 2, inserted: 2 });
    expect(stats[0]?.budgetStop).toMatch(/osm: daily limit reached \(2 of 2 calls used\)/);
    expect(stats[0]?.error).toBeUndefined();
    expect(db.select().from(sightings).all()).toHaveLength(2);
  });

  test("an aborted run stops instead of being reported as a provider failure", async () => {
    const osm = withBudget(fakeProvider("osm"), guard());

    await expect(discover(db, runId, [osm], config.places, place("goa"), AbortSignal.abort())).rejects.toThrow();
  });
});
