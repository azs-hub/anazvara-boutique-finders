import { type Db, sightings as sightingsTable } from "@anazvara/database";
import type { Sighting } from "../providers/types.js";

export interface SaveResult {
  inserted: number;
  alreadySeen: number;
  /** The sightings that were new, in input order. */
  insertedSightings: Sighting[];
}

/** The sightings row for one sighting: unmerged (no business yet), tagged with the run that found it. */
export function toRow(sighting: Sighting, runId: number): typeof sightingsTable.$inferInsert {
  return {
    businessId: null,
    source: sighting.source,
    externalId: sighting.externalId,
    placeKey: sighting.placeKey,
    runId,
    sourceUrl: sighting.url ?? null,
    query: sighting.query ?? null,
    name: sighting.name ?? null,
    address: sighting.address ?? null,
    lat: sighting.lat ?? null,
    lng: sighting.lng ?? null,
    phone: sighting.phone ?? null,
    website: sighting.website ?? null,
    instagram: sighting.instagram ?? null,
    rawJson: sighting.raw ?? {},
  };
}

/**
 * Saves sightings before any matching, so a crash later loses nothing. A
 * sighting already stored for the same source, external id and place is left
 * as it was (first run wins) and counted as already seen.
 */
export function saveSightings(db: Db, runId: number, sightings: readonly Sighting[]): SaveResult {
  return db.transaction((tx) => {
    const insertedSightings = sightings.filter(
      (sighting) =>
        tx
          .insert(sightingsTable)
          .values(toRow(sighting, runId))
          .onConflictDoNothing({
            target: [sightingsTable.source, sightingsTable.externalId, sightingsTable.placeKey],
          })
          .run().changes > 0,
    );
    return {
      inserted: insertedSightings.length,
      alreadySeen: sightings.length - insertedSightings.length,
      insertedSightings,
    };
  });
}
