import { cpSync, existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { getRun, openDb, sightings } from "@anazvara/database";
import { CONFIG_DIR, ConfigError, expandPlace, loadConfig, REPO_ROOT } from "@anazvara/shared";
import {
  budgetRows,
  databasePath,
  discoveryRows,
  sampleRows,
  describeAreas,
  parseNewCount,
  parseSources,
  placeRows,
  providerRows,
  resolvePlace,
  runConfig,
  runFind,
  type CliContext,
} from "../../src/cli/commands.js";
import { UserError } from "../../src/cli/errors.js";
import { BudgetGuard } from "../../src/providers/budget.js";
import type { ProviderFactories } from "../../src/providers/registry.js";
import { fakeProvider } from "../providers/fake.js";
import { ASSAGAO_ELEMENTS, fakeFetch, fixtureFetch, json, testOsm } from "../providers/overpass.js";

const { places, app } = loadConfig();

let dir: string;
let lines: string[];

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stockist-cli-"));
  lines = [];
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

function context(overrides: Partial<CliContext> = {}): CliContext {
  return { env: { DATABASE_PATH: join(dir, "test.db") }, out: (line) => lines.push(line), ...overrides };
}

describe("parseNewCount", () => {
  test("accepts a positive whole number", () => {
    expect(parseNewCount("50")).toBe(50);
  });

  test.each(["0", "-3", "2.5", "ten", ""])("rejects %j", (value) => {
    expect(() => parseNewCount(value)).toThrow(UserError);
  });
});

describe("parseSources", () => {
  test("undefined means all providers", () => {
    expect(parseSources(undefined)).toBeUndefined();
  });

  test("splits, trims, lowercases and removes duplicates", () => {
    expect(parseSources(" OSM, exa ,osm,")).toEqual(["osm", "exa"]);
  });

  test("an unknown provider lists the valid names", () => {
    expect(() => parseSources("osm,google")).toThrow(
      new UserError('Unknown provider "google" in --sources. Valid names: osm, searxng, places, exa, tavily'),
    );
  });

  test("an empty list is an error", () => {
    expect(() => parseSources(" , ")).toThrow(UserError);
  });
});

describe("resolvePlace", () => {
  test("matches key, name or alias, ignoring case", () => {
    expect(resolvePlace(places, "GOA").key).toBe("goa");
    expect(resolvePlace(places, "bombay").key).toBe("mumbai");
  });

  test("an unknown place lists the known keys", () => {
    expect(() => resolvePlace(places, "Atlantis")).toThrow(
      `Unknown place "Atlantis". Known places: ${places.map((place) => place.key).join(", ")}`,
    );
  });
});

describe("describeAreas", () => {
  test("counts areas by type", () => {
    const india = resolvePlace(places, "india");
    const cities = places.filter((place) => place.type === "city").length;
    expect(describeAreas(expandPlace(places, india))).toBe(`${places.length - 1} (1 region, ${cities} cities)`);
  });

  test("uses the singular for one area", () => {
    const goa = resolvePlace(places, "goa");
    expect(describeAreas([{ name: "Goa", type: "region", place: goa }])).toBe("1 (1 region)");
  });
});

describe("placeRows", () => {
  test("one row per place with neighbourhood count and aliases", () => {
    const goa = resolvePlace(places, "goa");
    expect(placeRows(places)).toContainEqual([
      "goa",
      "Goa",
      "region",
      "India",
      String(goa.neighbourhoods.length),
      "North Goa, South Goa",
    ]);
  });

  test("filters by type and shows a dash for no aliases", () => {
    expect(placeRows(places, "country")).toEqual([["india", "India", "country", "India", "0", "-"]]);
  });
});

describe("providerRows", () => {
  test("shows the reason only for inactive providers", () => {
    expect(
      providerRows([
        { name: "osm", active: true, reason: "active" },
        { name: "exa", active: false, reason: "disabled in config/app.json" },
      ]),
    ).toEqual([
      ["osm", "yes", "-"],
      ["exa", "no", "disabled in config/app.json"],
    ]);
  });
});

describe("budgetRows", () => {
  test("shows used/limit for today and the month and the month's cost", () => {
    const db = openDb(":memory:");
    const now = new Date("2026-11-11T10:00:00Z");
    const guard = new BudgetGuard(db, app.providers, () => now);
    guard.reserve("exa", 3);

    expect(budgetRows(guard)).toContainEqual([
      "exa",
      `3/${app.providers.exa.dailyLimit}`,
      `3/${app.providers.exa.monthlyLimit}`,
      (3 * app.providers.exa.costPerCallUsd).toFixed(2),
    ]);
    expect(budgetRows(guard)).toContainEqual(["osm", `0/${app.providers.osm.dailyLimit}`, `0/${app.providers.osm.monthlyLimit}`, "0.00"]);
    db.$client.close();
  });
});

describe("databasePath", () => {
  test("defaults to data/stockists.db at the repo root", () => {
    expect(databasePath({})).toBe(join(REPO_ROOT, "data", "stockists.db"));
    expect(databasePath({ DATABASE_PATH: "  " })).toBe(join(REPO_ROOT, "data", "stockists.db"));
  });

  test("resolves a relative DATABASE_PATH against the repo root", () => {
    expect(databasePath({ DATABASE_PATH: "data/other.db" })).toBe(join(REPO_ROOT, "data", "other.db"));
  });

  test("keeps :memory: as is", () => {
    expect(databasePath({ DATABASE_PATH: ":memory:" })).toBe(":memory:");
  });
});

describe("input errors", () => {
  test("find rejects bad input before creating the database", async () => {
    const ctx = context();
    await expect(runFind(ctx, { location: "atlantis", new: "50" })).rejects.toThrow(UserError);
    await expect(runFind(ctx, { location: "goa", new: "50", sources: "google" })).rejects.toThrow(UserError);
    expect(existsSync(join(dir, "test.db"))).toBe(false);
  });

  test("an invalid config file gives a readable ConfigError", () => {
    const configDir = join(dir, "config");
    cpSync(CONFIG_DIR, configDir, { recursive: true });
    writeFileSync(join(configDir, "app.json"), JSON.stringify({ ...app, fetch: { ...app.fetch, timeoutMs: -1 } }));

    expect(() => runConfig(context({ configDir }))).toThrow(ConfigError);
    expect(() => runConfig(context({ configDir }))).toThrow(/fetch\.timeoutMs/);
  });
});

describe("find", () => {
  const osmFrom = (fetchFn: typeof fetch): ProviderFactories => ({ osm: () => testOsm(fetchFn).provider });
  const named = ASSAGAO_ELEMENTS.length - 1;
  const row = (provider: string) => lines.find((line) => line.startsWith(`${provider} `));

  test("saves sightings, then reports them as already seen on a second run", async () => {
    const { fetch, requests } = fixtureFetch();
    const ctx = context({ factories: osmFrom(fetch) });

    await runFind(ctx, { location: "goa", new: "50", sources: "osm" });

    expect(requests).toHaveLength(1);
    expect(lines).toContain("Active providers: osm");
    expect(row("osm")?.split(/\s{2,}/)).toEqual(["osm", "1/1", "1", String(named), String(named), "0", "1", "ok"]);
    expect(lines).toContain(`New sightings (${named} of ${named}):`);
    expect(lines.some((line) => line.startsWith("No Nasties ") && line.includes("Mapusa-Assagao-Anjuna Road"))).toBe(true);
    expect(lines.some((line) => line.startsWith("Note: --new is not enforced yet."))).toBe(true);
    expect(lines.at(-1)).toBe(`Run #1 finished: ${named} sightings, ${named} new, 0 already seen.`);

    lines = [];
    await runFind(ctx, { location: "goa", new: "50", sources: "osm" });

    expect(row("osm")?.split(/\s{2,}/)).toEqual(["osm", "1/1", "1", String(named), "0", String(named), "1", "ok"]);
    expect(lines.some((line) => line.startsWith("New sightings"))).toBe(false);
    expect(lines.at(-1)).toBe(`Run #2 finished: ${named} sightings, 0 new, ${named} already seen.`);

    const db = openDb(join(dir, "test.db"));
    expect(db.select().from(sightings).all()).toHaveLength(named);
    expect(getRun(db, 2)?.statsJson).toMatchObject({
      sightings: named,
      newSightings: 0,
      alreadySeen: named,
      unnamed: 1,
      providers: [{ provider: "osm", queries: 1, calls: 1, found: named, inserted: 0, alreadySeen: named, unnamed: 1 }],
    });
    expect(new BudgetGuard(db, app.providers).usage("osm").dailyCalls).toBe(2);
    db.$client.close();
  });

  test("a failing provider is printed and the run still finishes", async () => {
    const { fetch } = fakeFetch(() => json("bad", 400));
    const ctx = context({ factories: osmFrom(fetch) });

    await runFind(ctx, { location: "goa", new: "50", sources: "osm" });

    expect(row("osm")).toMatch(/failed$/);
    expect(lines).toContain("  osm failed: Overpass returned HTTP 400: bad");
    expect(lines.at(-1)).toBe("Run #1 finished: 0 sightings, 0 new, 0 already seen.");
  });

  test("the budget stops a provider cleanly", async () => {
    const configDir = join(dir, "config");
    cpSync(CONFIG_DIR, configDir, { recursive: true });
    const tight = structuredClone(app);
    tight.providers.osm.dailyLimit = 1;
    writeFileSync(join(configDir, "app.json"), JSON.stringify(tight));
    const ctx = context({ configDir, factories: { osm: () => fakeProvider("osm", { supports: ["area"] }) } });

    await runFind(ctx, { location: "india", new: "50", sources: "osm" });

    expect(row("osm")?.split(/\s{2,}/).slice(0, 3)).toEqual(["osm", `1/${places.length - 1}`, "1"]);
    expect(row("osm")).toMatch(/budget limit$/);
    expect(lines).toContain("  osm stopped: osm: daily limit reached (1 of 1 calls used)");
  });
});

describe("discoveryRows and sampleRows", () => {
  test("format provider counts and status", () => {
    const base = { queries: 2, queriesDone: 1, calls: 2, found: 5, inserted: 3, alreadySeen: 2, unnamed: 1 };
    expect(discoveryRows([{ provider: "osm", ...base, error: "x" }])).toEqual([
      ["osm", "1/2", "2", "5", "3", "2", "1", "failed"],
    ]);
  });

  test("sample shows name and address, at most the sample size", () => {
    const sighting = { source: "osm" as const, externalId: "node/1", placeKey: "goa" };
    expect(sampleRows([{ ...sighting, name: "Rang" }, sighting, sighting], 2)).toEqual([
      ["Rang", "-"],
      ["-", "-"],
    ]);
  });
});
