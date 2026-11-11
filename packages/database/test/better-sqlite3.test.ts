import Database from "better-sqlite3";
import { expect, test } from "vitest";

test("better-sqlite3 native binding loads and runs a query", () => {
  const db = new Database(":memory:");
  const row = db.prepare("select 1 + 1 as result").get() as { result: number };
  db.close();

  expect(row.result).toBe(2);
});
