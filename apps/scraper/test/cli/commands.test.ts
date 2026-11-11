import { cpSync, existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { openDb } from "@anazvara/database";
import { CONFIG_DIR, ConfigError, expandPlace, loadConfig, REPO_ROOT } from "@anazvara/shared";
import {
  budgetRows,
  databasePath,
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
  test("find rejects bad input before creating the database", () => {
    const ctx = context();
    expect(() => runFind(ctx, { location: "atlantis", new: "50" })).toThrow(UserError);
    expect(() => runFind(ctx, { location: "goa", new: "50", sources: "google" })).toThrow(UserError);
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
