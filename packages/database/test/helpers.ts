import type { Db } from "../src/client.js";
import { startRun } from "../src/runs.js";
import { businesses, sightings } from "../src/schema.js";

export const NOW = "2026-11-05T00:00:00.000Z";

export function insertBusiness(db: Db, values: Partial<typeof businesses.$inferInsert> = {}) {
  const run = startRun(db, { command: "find", city: "Goa" });
  return db
    .insert(businesses)
    .values({ name: "Test Shop", firstSeenRun: run.id, updatedAt: NOW, ...values })
    .returning()
    .get();
}

let nextExternalId = 1;

/** Inserts a sighting with the required identity columns filled in (and a run of its own). */
export function insertSighting(db: Db, values: Partial<typeof sightings.$inferInsert> = {}) {
  const runId = values.runId ?? startRun(db, { command: "find", city: "goa" }).id;
  return db
    .insert(sightings)
    .values({ source: "osm", externalId: `node/${nextExternalId++}`, placeKey: "goa", runId, rawJson: {}, ...values })
    .returning()
    .get();
}
