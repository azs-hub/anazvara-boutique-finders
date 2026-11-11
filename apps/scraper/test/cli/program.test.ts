import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { UserError } from "../../src/cli/errors.js";
import { createProgram, describeError } from "../../src/cli/program.js";
import { ConfigError } from "@anazvara/shared";
import { openDb, getRun } from "@anazvara/database";
import { ASSAGAO_ELEMENTS, fixtureFetch, testOsm } from "../providers/overpass.js";

let dir: string;
let dbPath: string;
let lines: string[];

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stockist-program-"));
  dbPath = join(dir, "stockists.db");
  lines = [];
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

async function run(...args: string[]): Promise<void> {
  // No provider keys: the shell's environment must not leak into the test. OSM answers from the fixture.
  const program = createProgram({
    env: { DATABASE_PATH: dbPath },
    out: (line) => lines.push(line),
    factories: { osm: () => testOsm(fixtureFetch().fetch).provider },
  });
  await program.parseAsync(args, { from: "user" });
}

describe("find (smoke)", () => {
  test("find --location goa records a finished run in a temp database", async () => {
    await run("find", "--location", "goa");

    expect(lines).toContain("Place: Goa (region, India) [goa]");
    expect(lines.some((line) => line.startsWith("Searchable areas: 25 "))).toBe(true);
    expect(lines).toContain("  skipped searxng: SEARXNG_URL is not set");
    expect(lines).toContain("Active providers: osm");

    const db = openDb(dbPath);
    const stored = getRun(db, 1);
    db.$client.close();
    expect(stored).toMatchObject({
      command: "find",
      city: "goa",
      params: { location: "goa", placeKey: "goa", new: 50, sources: "all" },
      statsJson: { areas: 25, sightings: ASSAGAO_ELEMENTS.length - 1, newBusinesses: 0 },
    });
    expect(stored?.finishedAt).not.toBeNull();
  });

  test("--sources limits the providers that are reported", async () => {
    await run("find", "--location", "Bombay", "--sources", "exa", "--new", "5");
    expect(lines.filter((line) => line.startsWith("  skipped"))).toEqual(["  skipped exa: disabled in config/app.json"]);
    expect(lines).toContain("Target: 5 new boutiques");
  });
});

describe("other commands", () => {
  test("places --type city lists only cities", async () => {
    await run("places", "--type", "city");
    expect(lines[0]).toMatch(/^key\s+name\s+type\s+country\s+neighbourhoods\s+aliases$/);
    expect(lines.slice(2).every((line) => /^\S+\s+.+\s+city\s+/.test(line))).toBe(true);
  });

  test("db migrate creates the database, budget reads it", async () => {
    await run("db", "migrate");
    expect(lines).toEqual([`Created database: ${dbPath}`, "Migrations applied: 3 (up to date)"]);

    lines = [];
    await run("budget");
    expect(lines[0]).toMatch(/^provider\s+today\s+month\s+month USD$/);
    expect(lines).toHaveLength(2 + 5);
  });
});

describe("describeError", () => {
  test("input and config errors print only the message", () => {
    expect(describeError(new UserError("Unknown place"))).toEqual({ message: "error: Unknown place", exitCode: 1 });
    expect(describeError(new ConfigError("Invalid app.json"))).toEqual({ message: "error: Invalid app.json", exitCode: 1 });
  });

  test("unexpected errors keep their stack", () => {
    const error = new TypeError("boom");
    expect(describeError(error)).toEqual({ message: error.stack, exitCode: 1 });
  });
});
