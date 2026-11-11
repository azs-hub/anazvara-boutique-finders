import { eq } from "drizzle-orm";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { openDb, type Db } from "../src/client.js";
import { startRun } from "../src/runs.js";
import { apiUsage, BUSINESS_STATUSES, businesses, SIGHTING_SOURCES, sightings } from "../src/schema.js";

let db: Db;
const NOW = "2026-11-05T00:00:00.000Z";

function insertBusiness(mapsPlaceId: string | null = null) {
  const run = startRun(db, { city: "Goa" });
  return db
    .insert(businesses)
    .values({ name: "Test Shop", firstSeenRun: run.id, updatedAt: NOW, mapsPlaceId })
    .returning()
    .get();
}

beforeEach(() => {
  db = openDb(":memory:");
});

afterEach(() => {
  db.$client.close();
});

describe("businesses", () => {
  test("defaults status to new", () => {
    expect(insertBusiness().status).toBe("new");
  });

  test("allows many null place ids but rejects a repeated one", () => {
    insertBusiness();
    insertBusiness();
    insertBusiness("place-1");

    const run = startRun(db, { city: "Goa" });
    const insertDuplicate = db.$client.prepare(
      "insert into businesses (name, first_seen_run, updated_at, maps_place_id) values ('Copy', ?, ?, 'place-1')",
    );

    expect(() => insertDuplicate.run(run.id, NOW)).toThrow(/UNIQUE/);
  });

  const insertWithStatus = (status: string) => {
    const run = startRun(db, { city: "Goa" });
    db.$client
      .prepare("insert into businesses (name, first_seen_run, updated_at, status) values ('Shop', ?, ?, ?)")
      .run(run.id, NOW, status);
  };

  test("rejects an invalid status", () => {
    expect(() => insertWithStatus("maybe")).toThrow(/CHECK/);
  });

  test.each(BUSINESS_STATUSES)("accepts status %s", (status) => {
    expect(() => insertWithStatus(status)).not.toThrow();
  });

  test("cannot be deleted while a sighting points at it", () => {
    const business = insertBusiness();
    db.insert(sightings).values({ businessId: business.id, source: "osm", rawJson: {} }).run();

    const deleteBusiness = db.$client.prepare("delete from businesses where id = ?");

    expect(() => deleteBusiness.run(business.id)).toThrow(/FOREIGN KEY/);
  });
});

describe("sightings", () => {
  const insertWithSource = (source: string) =>
    db.$client.prepare("insert into sightings (source, raw_json) values (?, '{}')").run(source);

  test("can be saved before merge, without a business", () => {
    const row = db.insert(sightings).values({ source: "osm", rawJson: {} }).returning().get();

    expect(row.businessId).toBeNull();
  });

  test("rejects an invalid source", () => {
    expect(() => insertWithSource("myspace")).toThrow(/CHECK/);
  });

  test.each(SIGHTING_SOURCES)("accepts source %s", (source) => {
    expect(() => insertWithSource(source)).not.toThrow();
  });

  // Names the index because SQLite's planner prefers sightings_business_id_idx for this query.
  test("partial index returns only unmerged rows", () => {
    const business = insertBusiness();
    db.insert(sightings).values({ businessId: business.id, source: "osm", rawJson: {} }).run();
    const unmerged = db.insert(sightings).values({ source: "osm", rawJson: {} }).returning().get();

    const rows = db.$client
      .prepare("select id from sightings indexed by sightings_unmerged_idx where business_id is null")
      .all();

    expect(rows).toEqual([{ id: unmerged.id }]);
  });

  test("round-trips raw_json", () => {
    const business = insertBusiness();
    const raw = { tags: { shop: "boutique" }, ids: [1, 2] };

    db.insert(sightings).values({ businessId: business.id, source: "osm", rawJson: raw }).run();

    const row = db.select().from(sightings).where(eq(sightings.businessId, business.id)).get();
    expect(row?.rawJson).toEqual(raw);
  });
});

describe("api_usage", () => {
  const insertUsage = (provider: string, day: string) =>
    db.$client.prepare("insert into api_usage (provider, day) values (?, ?)").run(provider, day);

  test("keeps one row per provider and day", () => {
    insertUsage("exa", "2026-11-05");

    expect(() => insertUsage("exa", "2026-11-05")).toThrow(/UNIQUE/);
  });

  test("allows the same provider on another day and another provider on the same day", () => {
    insertUsage("exa", "2026-11-05");
    insertUsage("exa", "2026-11-06");
    insertUsage("tavily", "2026-11-05");

    expect(db.select().from(apiUsage).all()).toHaveLength(3);
  });

  test("starts counters at zero", () => {
    insertUsage("exa", "2026-11-05");

    expect(db.select().from(apiUsage).get()).toMatchObject({ calls: 0, costUsd: 0 });
  });
});
