import { eq } from "drizzle-orm";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { openDb, type Db } from "../src/client.js";
import { startRun } from "../src/runs.js";
import {
  apiUsage,
  CONFIDENCE_LEVELS,
  CRM_STATUSES,
  EVIDENCE_SOURCES,
  FIT_LEVELS,
  JOB_STATUSES,
  JOB_TYPES,
  jobs,
  PHYSICAL_STORE_VALUES,
  PRIORITIES,
  SIGHTING_SOURCES,
  sightings,
  SYSTEM_DECISIONS,
} from "../src/schema.js";
import { insertBusiness, NOW } from "./helpers.js";

let db: Db;

beforeEach(() => {
  db = openDb(":memory:");
});

afterEach(() => {
  db.$client.close();
});

// One case per CHECK constraint: table, column, allowed values, and a raw insert of one value.
type EnumCase = { column: string; values: readonly string[]; insert: (value: string) => void };

const businessColumn = (column: string, values: readonly string[]): EnumCase => ({
  column: `businesses.${column}`,
  values,
  insert: (value) => {
    const run = startRun(db, { command: "find", city: "Goa" });
    db.$client
      .prepare(`insert into businesses (name, first_seen_run, updated_at, ${column}) values ('Shop', ?, ?, ?)`)
      .run(run.id, NOW, value);
  },
});

const ENUM_CASES: EnumCase[] = [
  businessColumn("physical_store", PHYSICAL_STORE_VALUES),
  businessColumn("physical_store_confidence", CONFIDENCE_LEVELS),
  businessColumn("maps_match_confidence", CONFIDENCE_LEVELS),
  businessColumn("system_decision", SYSTEM_DECISIONS),
  businessColumn("crm_status", CRM_STATUSES),
  businessColumn("fit", FIT_LEVELS),
  businessColumn("priority", PRIORITIES),
  {
    column: "sightings.source",
    values: SIGHTING_SOURCES,
    insert: (value) => db.$client.prepare("insert into sightings (source, raw_json) values (?, '{}')").run(value),
  },
  {
    column: "evidence.source",
    values: EVIDENCE_SOURCES,
    insert: (value) => insertEvidence(value, "high"),
  },
  {
    column: "evidence.confidence",
    values: CONFIDENCE_LEVELS,
    insert: (value) => insertEvidence("website", value),
  },
  { column: "jobs.type", values: JOB_TYPES, insert: (value) => insertJob(value, "queued") },
  { column: "jobs.status", values: JOB_STATUSES, insert: (value) => insertJob("find", value) },
];

function insertEvidence(source: string, confidence: string): void {
  const business = insertBusiness(db);
  db.$client
    .prepare(
      "insert into evidence (business_id, field, value, source, confidence, created_at) values (?, 'address', 'x', ?, ?, ?)",
    )
    .run(business.id, source, confidence, NOW);
}

function insertJob(type: string, status: string): void {
  db.$client
    .prepare("insert into jobs (type, status, params, created_at) values (?, ?, '{}', ?)")
    .run(type, status, NOW);
}

describe.each(ENUM_CASES)("$column CHECK", ({ values, insert }) => {
  test("rejects an invalid value", () => {
    expect(() => insert("maybe")).toThrow(/CHECK/);
  });

  test.each(values)("accepts %s", (value) => {
    expect(() => insert(value)).not.toThrow();
  });
});

describe("businesses", () => {
  test("starts unknown, unscored, outside the CRM and contactable", () => {
    expect(insertBusiness(db)).toMatchObject({
      physicalStore: "unknown",
      physicalStoreConfidence: null,
      systemDecision: null,
      crmStatus: null,
      fit: null,
      priority: null,
      doNotContact: false,
    });
  });

  test("allows many null place ids but rejects a repeated one", () => {
    insertBusiness(db);
    insertBusiness(db);
    insertBusiness(db, { mapsPlaceId: "place-1" });

    expect(() => insertBusiness(db, { mapsPlaceId: "place-1" })).toThrow(/UNIQUE/);
  });

  test("has an index on system_decision + priority", () => {
    const columns = db.$client.prepare("select name from pragma_index_info('businesses_decision_priority_idx')").all();

    expect(columns).toEqual([{ name: "system_decision" }, { name: "priority" }]);
  });

  test("cannot be deleted while a sighting points at it", () => {
    const business = insertBusiness(db);
    db.insert(sightings).values({ businessId: business.id, source: "osm", rawJson: {} }).run();

    const deleteBusiness = db.$client.prepare("delete from businesses where id = ?");

    expect(() => deleteBusiness.run(business.id)).toThrow(/FOREIGN KEY/);
  });

  test("cannot be deleted while evidence points at it", () => {
    const business = insertBusiness(db);
    db.$client
      .prepare(
        "insert into evidence (business_id, field, value, source, created_at) values (?, 'physical_store', 'yes', 'website', ?)",
      )
      .run(business.id, NOW);

    const deleteBusiness = db.$client.prepare("delete from businesses where id = ?");

    expect(() => deleteBusiness.run(business.id)).toThrow(/FOREIGN KEY/);
  });
});

describe("sightings", () => {
  test("can be saved before merge, without a business", () => {
    const row = db.insert(sightings).values({ source: "osm", rawJson: {} }).returning().get();

    expect(row.businessId).toBeNull();
  });

  // Names the index because SQLite's planner prefers sightings_business_id_idx for this query.
  test("partial index returns only unmerged rows", () => {
    const business = insertBusiness(db);
    db.insert(sightings).values({ businessId: business.id, source: "osm", rawJson: {} }).run();
    const unmerged = db.insert(sightings).values({ source: "osm", rawJson: {} }).returning().get();

    const rows = db.$client
      .prepare("select id from sightings indexed by sightings_unmerged_idx where business_id is null")
      .all();

    expect(rows).toEqual([{ id: unmerged.id }]);
  });

  test("round-trips raw_json", () => {
    const business = insertBusiness(db);
    const raw = { tags: { shop: "boutique" }, ids: [1, 2] };

    db.insert(sightings).values({ businessId: business.id, source: "osm", rawJson: raw }).run();

    const row = db.select().from(sightings).where(eq(sightings.businessId, business.id)).get();
    expect(row?.rawJson).toEqual(raw);
  });
});

describe("jobs", () => {
  test("a new job is queued, with no progress, error or run", () => {
    const job = db
      .insert(jobs)
      .values({ type: "find", params: { location: "goa" }, createdAt: NOW })
      .returning()
      .get();

    expect(job).toMatchObject({
      type: "find",
      params: { location: "goa" },
      status: "queued",
      progress: null,
      error: null,
      runId: null,
      startedAt: null,
      finishedAt: null,
    });
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
