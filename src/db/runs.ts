import { eq } from "drizzle-orm";
import type { Db } from "./client.js";
import { runs } from "./schema.js";

export type Run = typeof runs.$inferSelect;

type StartRunInput = {
  city: string;
  params?: Record<string, unknown>;
  now?: Date;
};

export function startRun(db: Db, { city, params = {}, now = new Date() }: StartRunInput): Run {
  return db.insert(runs).values({ city, params, startedAt: now.toISOString() }).returning().get();
}

export function finishRun(db: Db, id: number, stats: Record<string, unknown>, now = new Date()): Run {
  const run = db
    .update(runs)
    .set({ finishedAt: now.toISOString(), statsJson: stats })
    .where(eq(runs.id, id))
    .returning()
    .get();

  if (!run) {
    throw new Error(`Run ${id} not found`);
  }
  return run;
}

export function getRun(db: Db, id: number): Run | undefined {
  return db.select().from(runs).where(eq(runs.id, id)).get();
}
