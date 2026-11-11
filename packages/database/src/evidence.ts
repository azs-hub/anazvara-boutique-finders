import { and, asc, eq } from "drizzle-orm";
import type { Db } from "./client.js";
import { evidence } from "./schema.js";

export type Evidence = typeof evidence.$inferSelect;
export type NewEvidence = Omit<typeof evidence.$inferInsert, "id" | "createdAt"> & { now?: Date };

export function addEvidence(db: Db, { now = new Date(), ...row }: NewEvidence): Evidence {
  return db
    .insert(evidence)
    .values({ ...row, createdAt: now.toISOString() })
    .returning()
    .get();
}

/** Evidence for a business, oldest first, optionally for one field only. */
export function listEvidence(db: Db, businessId: number, field?: string): Evidence[] {
  const forBusiness = eq(evidence.businessId, businessId);
  return db
    .select()
    .from(evidence)
    .where(field === undefined ? forBusiness : and(forBusiness, eq(evidence.field, field)))
    .orderBy(asc(evidence.id))
    .all();
}
