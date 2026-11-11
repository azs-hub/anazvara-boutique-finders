import { eq, getTableColumns } from "drizzle-orm";
import type { Db } from "./client.js";
import { businesses, overrides } from "./schema.js";

export type Business = typeof businesses.$inferSelect;
export type Override = typeof overrides.$inferSelect;
/** Overridden values keyed by businesses column name, e.g. { physical_store: "yes" }. */
export type Overrides = Record<string, unknown>;

// Bookkeeping columns the user cannot lock.
const NOT_OVERRIDABLE = new Set(["id", "first_seen_run", "updated_at"]);

// businesses column name → property name on a Business row, e.g. physical_store → physicalStore.
const PROPERTY_BY_COLUMN = new Map(
  Object.entries(getTableColumns(businesses)).map(([property, column]) => [column.name, property]),
);

export function isOverridableField(field: string): boolean {
  return PROPERTY_BY_COLUMN.has(field) && !NOT_OVERRIDABLE.has(field);
}

/** Locks a business field to a user value. A second call for the same field replaces the value. */
export function setOverride(
  db: Db,
  businessId: number,
  field: string,
  value: unknown,
  note?: string,
  now = new Date(),
): Override {
  if (!isOverridableField(field)) {
    throw new Error(`Cannot override businesses.${field}`);
  }
  const row = { businessId, field, value, note: note ?? null, editedAt: now.toISOString() };
  return db
    .insert(overrides)
    .values(row)
    .onConflictDoUpdate({
      target: [overrides.businessId, overrides.field],
      set: { value: row.value, note: row.note, editedAt: row.editedAt },
    })
    .returning()
    .get();
}

export function getOverrides(db: Db, businessId: number): Overrides {
  const rows = db.select().from(overrides).where(eq(overrides.businessId, businessId)).all();
  return Object.fromEntries(rows.map((row) => [row.field, row.value]));
}

/**
 * Returns a copy of `business` with the user's values on top. Fields that are not businesses
 * columns (e.g. one dropped from the schema later) are ignored. Values are not type-checked here.
 */
export function applyOverrides(business: Business, userValues: Overrides): Business {
  const merged: Record<string, unknown> = { ...business };
  for (const [field, value] of Object.entries(userValues)) {
    const property = PROPERTY_BY_COLUMN.get(field);
    if (property !== undefined && isOverridableField(field)) merged[property] = value;
  }
  return merged as Business;
}
