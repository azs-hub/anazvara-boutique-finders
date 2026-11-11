import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import * as schema from "./schema.js";

const MIGRATIONS_FOLDER = fileURLToPath(new URL("../drizzle", import.meta.url));

export type Db = ReturnType<typeof openDb>;

/** Opens the database at `path` (or ":memory:") and applies pending migrations. */
export function openDb(path: string) {
  if (path !== ":memory:") {
    mkdirSync(dirname(path), { recursive: true });
  }

  const sqlite = new Database(path);
  try {
    sqlite.pragma("journal_mode = WAL");
    sqlite.pragma("foreign_keys = ON");

    const db = drizzle(sqlite, { schema });
    migrate(db, { migrationsFolder: MIGRATIONS_FOLDER });
    return db;
  } catch (error) {
    sqlite.close();
    throw error;
  }
}
