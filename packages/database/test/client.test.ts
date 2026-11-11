import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, test } from "vitest";
import { openDb, type Db } from "../src/client.js";

const opened: Db[] = [];
const tempDirs: string[] = [];

function open(path: string): Db {
  const db = openDb(path);
  opened.push(db);
  return db;
}

function tempDbPath(): string {
  const dir = mkdtempSync(join(tmpdir(), "stockist-db-"));
  tempDirs.push(dir);
  return join(dir, "nested", "test.db");
}

afterEach(() => {
  for (const db of opened.splice(0)) db.$client.close();
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("openDb", () => {
  test("applies migrations so every table exists", () => {
    const db = open(":memory:");

    const tables = db.$client
      .prepare("select name from sqlite_master where type = 'table' and name not like '\\_\\_%' escape '\\' and name not like 'sqlite_%'")
      .all() as { name: string }[];

    expect(tables.map((table) => table.name).sort()).toEqual([
      "api_usage",
      "businesses",
      "evidence",
      "jobs",
      "overrides",
      "pages",
      "qualifications",
      "runs",
      "sightings",
    ]);
  });

  test("enforces foreign keys", () => {
    const db = open(":memory:");

    expect(db.$client.pragma("foreign_keys", { simple: true })).toBe(1);
    expect(() =>
      db.$client
        .prepare("insert into sightings (business_id, source, raw_json) values (999, 'osm', '{}')")
        .run(),
    ).toThrow(/FOREIGN KEY/);
  });

  // WAL does not apply to in-memory databases, so these two need a real file.
  test("uses WAL for a file database and creates its parent folder", () => {
    const db = open(tempDbPath());

    expect(db.$client.pragma("journal_mode", { simple: true })).toBe("wal");
  });

  test("reopening a migrated file keeps its data", () => {
    const path = tempDbPath();
    const first = openDb(path);
    first.$client.prepare("insert into api_usage (provider, day) values ('exa', '2026-11-05')").run();
    first.$client.close();

    const reopened = open(path);

    expect(reopened.$client.prepare("select count(*) as n from api_usage").get()).toEqual({ n: 1 });
  });
});
