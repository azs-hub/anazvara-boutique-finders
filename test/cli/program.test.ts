import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { UserError } from "../../src/cli/errors.js";
import { createProgram, describeError } from "../../src/cli/program.js";
import { ConfigError } from "../../src/config/load.js";
import { openDb } from "../../src/db/client.js";
import { getRun } from "../../src/db/runs.js";

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
  // No provider keys: the shell's environment must not leak into the test.
  const program = createProgram({ env: { DATABASE_PATH: dbPath }, out: (line) => lines.push(line) });
  await program.parseAsync(args, { from: "user" });
}

describe("find (smoke)", () => {
  test("find --location goa records a finished run in a temp database", async () => {
    await run("find", "--location", "goa");

    expect(lines).toContain("Place: Goa (region, India) [goa]");
    expect(lines.some((line) => line.startsWith("Searchable areas: 25 "))).toBe(true);
    expect(lines).toContain("  skipped searxng: SEARXNG_URL is not set");
    expect(lines).toContain("  skipped osm: not implemented yet");
    expect(lines).toContain("Discovery providers arrive in Phase 2: nothing was searched.");

    const db = openDb(dbPath);
    const stored = getRun(db, 1);
    db.$client.close();
    expect(stored).toMatchObject({
      city: "goa",
      params: { command: "find", location: "goa", placeKey: "goa", new: 50, sources: "all" },
      statsJson: { areas: 25, providers: [], sightings: 0, newBusinesses: 0 },
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
    expect(lines).toEqual([`Created database: ${dbPath}`, "Migrations applied: 1 (up to date)"]);

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
