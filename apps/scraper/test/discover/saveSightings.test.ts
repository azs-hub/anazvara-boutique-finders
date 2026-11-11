import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { openDb, sightings, startRun, type Db } from "@anazvara/database";
import { saveSightings } from "../../src/discover/saveSightings.js";
import type { Sighting } from "../../src/providers/types.js";

let db: Db;

beforeEach(() => {
  db = openDb(":memory:");
});

afterEach(() => {
  db.$client.close();
});

const newRun = () => startRun(db, { command: "find", city: "goa" }).id;

const rang: Sighting = {
  source: "osm",
  externalId: "node/1",
  placeKey: "goa",
  query: "ql",
  url: "https://www.openstreetmap.org/node/1",
  name: "Rang",
  address: "Assagao",
  lat: 15.59,
  lng: 73.77,
  phone: "+91 1",
  website: "https://rang.example",
  instagram: "rang",
  raw: { name: "Rang", shop: "clothes" },
};
const bare: Sighting = { source: "osm", externalId: "way/2", placeKey: "goa" };

describe("saveSightings", () => {
  test("stores each sighting unmerged, with its run and every field", () => {
    const runId = newRun();

    const result = saveSightings(db, runId, [rang, bare]);

    expect(result).toEqual({ inserted: 2, alreadySeen: 0, insertedSightings: [rang, bare] });
    expect(db.select().from(sightings).all()).toEqual([
      {
        id: 1,
        businessId: null,
        source: "osm",
        externalId: "node/1",
        placeKey: "goa",
        runId,
        sourceUrl: "https://www.openstreetmap.org/node/1",
        query: "ql",
        name: "Rang",
        address: "Assagao",
        lat: 15.59,
        lng: 73.77,
        phone: "+91 1",
        website: "https://rang.example",
        instagram: "rang",
        rawJson: { name: "Rang", shop: "clothes" },
      },
      expect.objectContaining({ externalId: "way/2", name: null, rawJson: {} }),
    ]);
  });

  test("a sighting seen before is counted as already seen and left as it was", () => {
    const first = newRun();
    saveSightings(db, first, [rang]);
    const second = newRun();

    const result = saveSightings(db, second, [{ ...rang, name: "Rang (renamed)" }, bare]);

    expect(result).toMatchObject({ inserted: 1, alreadySeen: 1, insertedSightings: [bare] });
    const rows = db.select().from(sightings).all();
    expect(rows.map((row) => [row.externalId, row.runId, row.name])).toEqual([
      ["node/1", first, "Rang"],
      ["way/2", second, null],
    ]);
  });

  test("the same element in another place or from another source is a new sighting", () => {
    const runId = newRun();
    saveSightings(db, runId, [rang]);

    expect(saveSightings(db, runId, [{ ...rang, placeKey: "india" }, { ...rang, source: "places" }]).inserted).toBe(2);
  });

  test("nothing to save is fine", () => {
    expect(saveSightings(db, newRun(), [])).toEqual({ inserted: 0, alreadySeen: 0, insertedSightings: [] });
  });
});
