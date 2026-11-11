import type { Db } from "../src/client.js";
import { startRun } from "../src/runs.js";
import { businesses } from "../src/schema.js";

export const NOW = "2026-11-05T00:00:00.000Z";

export function insertBusiness(db: Db, values: Partial<typeof businesses.$inferInsert> = {}) {
  const run = startRun(db, { command: "find", city: "Goa" });
  return db
    .insert(businesses)
    .values({ name: "Test Shop", firstSeenRun: run.id, updatedAt: NOW, ...values })
    .returning()
    .get();
}
