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

export const BUSINESS_STATUSES = ["new", "qualified", "lead", "review", "rejected", "contacted"] as const;
export const SIGHTING_SOURCES = ["osm", "places", "searxng", "exa", "tavily", "guide"] as const;

type JsonObject = Record<string, unknown>;

function isOneOf(column: AnySQLiteColumn, values: readonly string[]): SQL {
  const list = values.map((value) => `'${value}'`).join(", ");
  return sql`${column} in (${sql.raw(list)})`;
}

// Foreign keys block deletes (no cascade). A merge must move sightings, pages and
// qualifications to the surviving business before deleting the other one.

export const runs = sqliteTable("runs", {
  id: integer("id").primaryKey({ autoIncrement: true }),
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
    city: text("city"),
    locality: text("locality"),
    address: text("address"),
    lat: real("lat"),
    lng: real("lng"),
    websiteDomain: text("website_domain"),
    instagram: text("instagram"),
    phone: text("phone"),
    email: text("email"),
    mapsPlaceId: text("maps_place_id"),
    status: text("status", { enum: BUSINESS_STATUSES }).notNull().default("new"),
    score: real("score"),
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
    index("businesses_city_status_idx").on(table.city, table.status),
    check("businesses_status_check", isOneOf(table.status, BUSINESS_STATUSES)),
  ],
);

export const sightings = sqliteTable(
  "sightings",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    // Null until merge links the sighting to a business.
    businessId: integer("business_id").references(() => businesses.id),
    source: text("source", { enum: SIGHTING_SOURCES }).notNull(),
    sourceUrl: text("source_url"),
    query: text("query"),
    rawJson: text("raw_json", { mode: "json" }).$type<unknown>().notNull(),
  },
  (table) => [
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
