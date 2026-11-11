import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { afterEach, describe, expect, test } from "vitest";
import { openDb } from "../src/client.js";

const MIGRATIONS = fileURLToPath(new URL("../drizzle", import.meta.url));
const tempDirs: string[] = [];

afterEach(() => {
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

/** A migrations folder holding only 0000_init, i.e. the schema as it was before 0001. */
function initOnlyFolder(dir: string): string {
  const folder = join(dir, "drizzle");
  mkdirSync(join(folder, "meta"), { recursive: true });
  const journal = JSON.parse(readFileSync(join(MIGRATIONS, "meta", "_journal.json"), "utf8")) as {
    entries: { tag: string }[];
  };
  const init = journal.entries.filter((entry) => entry.tag === "0000_init");
  writeFileSync(join(folder, "meta", "_journal.json"), JSON.stringify({ ...journal, entries: init }));
  copyFileSync(join(MIGRATIONS, "0000_init.sql"), join(folder, "0000_init.sql"));
  return folder;
}

describe("0001_crm_evidence_overrides_jobs", () => {
  test("applies on top of a database with runs and backfills runs.command from params", () => {
    const dir = mkdtempSync(join(tmpdir(), "stockist-migrate-"));
    tempDirs.push(dir);
    const path = join(dir, "old.db");

    const old = new Database(path);
    migrate(drizzle(old), { migrationsFolder: initOnlyFolder(dir) });
    const insertRun = old.prepare("insert into runs (city, started_at, params) values ('goa', ?, ?)");
    insertRun.run("2026-11-05T10:00:00.000Z", JSON.stringify({ command: "find", location: "goa" }));
    insertRun.run("2026-11-05T11:00:00.000Z", JSON.stringify({ location: "goa" }));
    old.close();

    const db = openDb(path);
    const rows = db.$client.prepare("select id, command, params from runs order by id").all();
    db.$client.close();

    expect(rows).toEqual([
      { id: 1, command: "find", params: JSON.stringify({ command: "find", location: "goa" }) },
      // Only `find` created runs before this migration.
      { id: 2, command: "find", params: JSON.stringify({ location: "goa" }) },
    ]);
  });
});
