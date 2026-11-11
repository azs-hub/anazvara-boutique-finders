import { isNull, sql, type SQL } from "drizzle-orm";
import {
  check,
  index,
  integer,
  primaryKey,
  real,
  sqliteTable,
  text,
  uniqueIndex,
  type AnySQLiteColumn,
} from "drizzle-orm/sqlite-core";

export const PHYSICAL_STORE_VALUES = ["yes", "no", "unknown"] as const;
export const CONFIDENCE_LEVELS = ["high", "medium", "low"] as const;
// Set by the scraper and recalculated each run (qualify + score stages only).
export const SYSTEM_DECISIONS = ["lead", "review", "excluded"] as const;
// Set only by the user; the scraper never writes it.
export const CRM_STATUSES = [
  "to_prospect",
  "contacted",
  "followed_up",
  "qualified",
  "in_discussion",
  "offer_sent",
  "to_recontact",
  "lost",
  "invalid",
] as const;
export const FIT_LEVELS = ["high", "medium", "low", "exclude"] as const;
export const PRIORITIES = ["top", "high", "medium", "low", "exclude"] as const;
export const SIGHTING_SOURCES = ["osm", "places", "searxng", "exa", "tavily", "guide", "manual"] as const;
export const EVIDENCE_SOURCES = [
  "osm",
  "places",
  "searxng",
  "exa",
  "tavily",
  "guide",
  "website",
  "llm",
  "user",
  "manual",
] as const;
export const JOB_TYPES = ["find", "verify", "qualify", "export"] as const;
export const JOB_STATUSES = ["queued", "running", "done", "failed", "cancelled"] as const;

export type PhysicalStore = (typeof PHYSICAL_STORE_VALUES)[number];
export type Confidence = (typeof CONFIDENCE_LEVELS)[number];
export type SystemDecision = (typeof SYSTEM_DECISIONS)[number];
export type CrmStatus = (typeof CRM_STATUSES)[number];
export type Fit = (typeof FIT_LEVELS)[number];
export type Priority = (typeof PRIORITIES)[number];
export type SightingSource = (typeof SIGHTING_SOURCES)[number];
export type EvidenceSource = (typeof EVIDENCE_SOURCES)[number];
export type JobType = (typeof JOB_TYPES)[number];
export type JobStatus = (typeof JOB_STATUSES)[number];

type JsonObject = Record<string, unknown>;

// The column name is unqualified on purpose. drizzle-kit rebuilds a table as `__new_<table>` and then
// renames it, and SQLite rejects a rename when a CHECK still says "__new_<table>"."column".
function isOneOf(column: AnySQLiteColumn, values: readonly string[]): SQL {
  const list = values.map((value) => `'${value}'`).join(", ");
  return sql`${sql.identifier(column.name)} in (${sql.raw(list)})`;
}

// Foreign keys block deletes (no cascade). A merge must move sightings, pages, qualifications,
// evidence and overrides to the surviving business before deleting the other one.

export const runs = sqliteTable("runs", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  // The CLI command that started the run, e.g. "find".
  command: text("command").notNull(),
  city: text("city").notNull(),
  startedAt: text("started_at").notNull(),
  finishedAt: text("finished_at"),
  params: text("params", { mode: "json" }).$type<JsonObject>().notNull(),
  statsJson: text("stats_json", { mode: "json" }).$type<JsonObject>(),
});

export const businesses = sqliteTable(
  "businesses",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    name: text("name").notNull(),
    country: text("country"),
    // State or region.
    region: text("region"),
    city: text("city"),
    locality: text("locality"),
    address: text("address"),
    lat: real("lat"),
    lng: real("lng"),
    // Free text: store types are configured per market, not fixed here.
    storeType: text("store_type"),
    websiteDomain: text("website_domain"),
    instagram: text("instagram"),
    phone: text("phone"),
    whatsapp: text("whatsapp"),
    email: text("email"),
    contactPerson: text("contact_person"),
    contactRole: text("contact_role"),
    // Google Maps (main spec §7.1): only the place id and the Maps URL are stored permanently.
    // Never add columns for the Maps name, address, phone, rating or reviews.
    mapsPlaceId: text("maps_place_id"),
    mapsUrl: text("maps_url"),
    mapsVerifiedAt: text("maps_verified_at"),
    mapsMatchConfidence: text("maps_match_confidence", { enum: CONFIDENCE_LEVELS }),
    physicalStore: text("physical_store", { enum: PHYSICAL_STORE_VALUES }).notNull().default("unknown"),
    physicalStoreConfidence: text("physical_store_confidence", { enum: CONFIDENCE_LEVELS }),
    verifiedAt: text("verified_at"),
    score: real("score"),
    fit: text("fit", { enum: FIT_LEVELS }),
    priority: text("priority", { enum: PRIORITIES }),
    // Null until scored.
    systemDecision: text("system_decision", { enum: SYSTEM_DECISIONS }),
    // CRM fields: set only by the user.
    crmStatus: text("crm_status", { enum: CRM_STATUSES }),
    lastContacted: text("last_contacted"),
    nextFollowUp: text("next_follow_up"),
    doNotContact: integer("do_not_contact", { mode: "boolean" }).notNull().default(false),
    firstSeenRun: integer("first_seen_run")
      .notNull()
      .references(() => runs.id),
    updatedAt: text("updated_at").notNull(),
  },
  (table) => [
    uniqueIndex("businesses_maps_place_id_unique").on(table.mapsPlaceId),
    index("businesses_website_domain_idx").on(table.websiteDomain),
    index("businesses_instagram_idx").on(table.instagram),
    index("businesses_phone_idx").on(table.phone),
    index("businesses_city_idx").on(table.city),
    index("businesses_decision_priority_idx").on(table.systemDecision, table.priority),
    check("businesses_physical_store_check", isOneOf(table.physicalStore, PHYSICAL_STORE_VALUES)),
    check(
      "businesses_physical_store_confidence_check",
      isOneOf(table.physicalStoreConfidence, CONFIDENCE_LEVELS),
    ),
    check("businesses_maps_match_confidence_check", isOneOf(table.mapsMatchConfidence, CONFIDENCE_LEVELS)),
    check("businesses_system_decision_check", isOneOf(table.systemDecision, SYSTEM_DECISIONS)),
    check("businesses_crm_status_check", isOneOf(table.crmStatus, CRM_STATUSES)),
    check("businesses_fit_check", isOneOf(table.fit, FIT_LEVELS)),
    check("businesses_priority_check", isOneOf(table.priority, PRIORITIES)),
  ],
);

export const sightings = sqliteTable(
  "sightings",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    // Null until merge links the sighting to a business.
    businessId: integer("business_id").references(() => businesses.id),
    source: text("source", { enum: SIGHTING_SOURCES }).notNull(),
    // Stable id within the source, e.g. OSM "node/123", a place id or a normalized URL.
    externalId: text("external_id").notNull(),
    // Key of the catalog place that was searched (config/places.json).
    placeKey: text("place_key").notNull(),
    // The run that first saw this sighting.
    runId: integer("run_id")
      .notNull()
      .references(() => runs.id),
    sourceUrl: text("source_url"),
    query: text("query"),
    // What the source said, as is. Matching (Phase 2b) reads these; they are not verified facts.
    name: text("name"),
    address: text("address"),
    lat: real("lat"),
    lng: real("lng"),
    phone: text("phone"),
    website: text("website"),
    instagram: text("instagram"),
    rawJson: text("raw_json", { mode: "json" }).$type<unknown>().notNull(),
  },
  (table) => [
    // The same find from the same source for the same place is saved once, however many runs see it.
    uniqueIndex("sightings_source_external_id_place_key_unique").on(table.source, table.externalId, table.placeKey),
    index("sightings_business_id_idx").on(table.businessId),
    index("sightings_unmerged_idx").on(table.id).where(isNull(table.businessId)),
    check("sightings_source_check", isOneOf(table.source, SIGHTING_SOURCES)),
  ],
);

export const pages = sqliteTable(
  "pages",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    url: text("url").notNull(),
    businessId: integer("business_id")
      .notNull()
      .references(() => businesses.id),
    fetchedAt: text("fetched_at").notNull(),
    status: integer("status"),
    text: text("text"),
    jsonld: text("jsonld", { mode: "json" }).$type<unknown>(),
  },
  (table) => [uniqueIndex("pages_business_id_url_unique").on(table.businessId, table.url)],
);

export const qualifications = sqliteTable(
  "qualifications",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    businessId: integer("business_id")
      .notNull()
      .references(() => businesses.id),
    model: text("model").notNull(),
    json: text("json", { mode: "json" }).$type<unknown>().notNull(),
    createdAt: text("created_at").notNull(),
  },
  (table) => [index("qualifications_business_id_idx").on(table.businessId)],
);

// One row per observed fact about a business, e.g. field "physical_store", value "yes", with a quote.
export const evidence = sqliteTable(
  "evidence",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    businessId: integer("business_id")
      .notNull()
      .references(() => businesses.id),
    field: text("field").notNull(),
    value: text("value").notNull(),
    source: text("source", { enum: EVIDENCE_SOURCES }).notNull(),
    sourceUrl: text("source_url"),
    quote: text("quote"),
    confidence: text("confidence", { enum: CONFIDENCE_LEVELS }),
    sightingId: integer("sighting_id").references(() => sightings.id),
    pageId: integer("page_id").references(() => pages.id),
    runId: integer("run_id").references(() => runs.id),
    createdAt: text("created_at").notNull(),
  },
  (table) => [
    index("evidence_business_id_field_idx").on(table.businessId, table.field),
    check("evidence_source_check", isOneOf(table.source, EVIDENCE_SOURCES)),
    check("evidence_confidence_check", isOneOf(table.confidence, CONFIDENCE_LEVELS)),
  ],
);

// The user-lock: a business field the user edited. Later runs must never overwrite it.
// `field` is the businesses column name, e.g. "physical_store".
export const overrides = sqliteTable(
  "overrides",
  {
    businessId: integer("business_id")
      .notNull()
      .references(() => businesses.id),
    field: text("field").notNull(),
    // Nullable so the user can lock a field to empty: the row's existence is the lock.
    value: text("value", { mode: "json" }).$type<unknown>(),
    editedAt: text("edited_at").notNull(),
    note: text("note"),
  },
  (table) => [primaryKey({ columns: [table.businessId, table.field] })],
);

export const jobs = sqliteTable(
  "jobs",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    type: text("type", { enum: JOB_TYPES }).notNull(),
    params: text("params", { mode: "json" }).$type<JsonObject>().notNull(),
    status: text("status", { enum: JOB_STATUSES }).notNull().default("queued"),
    progress: text("progress", { mode: "json" }).$type<JsonObject>(),
    error: text("error"),
    runId: integer("run_id").references(() => runs.id),
    createdAt: text("created_at").notNull(),
    startedAt: text("started_at"),
    finishedAt: text("finished_at"),
  },
  (table) => [
    index("jobs_status_created_at_idx").on(table.status, table.createdAt),
    check("jobs_type_check", isOneOf(table.type, JOB_TYPES)),
    check("jobs_status_check", isOneOf(table.status, JOB_STATUSES)),
  ],
);

export const apiUsage = sqliteTable(
  "api_usage",
  {
    provider: text("provider").notNull(),
    day: text("day").notNull(),
    calls: integer("calls").notNull().default(0),
    costUsd: real("cost_usd").notNull().default(0),
  },
  (table) => [primaryKey({ columns: [table.provider, table.day] })],
);
