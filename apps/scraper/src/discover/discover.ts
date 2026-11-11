import type { Db } from "@anazvara/database";
import { bboxAreas, type Place, type ProviderName } from "@anazvara/shared";
import { BudgetExceededError } from "../providers/budget.js";
import type { BudgetedProvider, ProviderQuery, Sighting } from "../providers/types.js";
import { saveSightings } from "./saveSightings.js";

export interface ProviderStats {
  provider: ProviderName;
  /** Queries planned for this provider. */
  queries: number;
  /** Queries that returned results. */
  queriesDone: number;
  /** Calls reserved against the budget, retries included. */
  calls: number;
  found: number;
  inserted: number;
  alreadySeen: number;
  unnamed: number;
  /** Set when the budget stopped the provider. */
  budgetStop?: string;
  /** Set when the provider failed. */
  error?: string;
}

export interface DiscoverResult {
  stats: ProviderStats[];
  /** New sightings from every provider, in the order they were found. */
  inserted: Sighting[];
}

/**
 * The queries a provider gets for a place. Area queries use every area with a
 * bbox. Text queries come from the market's vocabulary in Phase 3; until then
 * a text-only provider gets none.
 */
export function queriesFor(
  provider: Pick<BudgetedProvider, "supports">,
  places: readonly Place[],
  place: Place,
): ProviderQuery[] {
  if (!provider.supports.includes("area")) return [];
  return bboxAreas(places, place).map((area) => ({ kind: "area", area }));
}

/**
 * Runs each provider over its queries, one at a time, and saves what it finds.
 * A provider that fails or hits its budget stops; the others still run.
 */
export async function discover(
  db: Db,
  runId: number,
  providers: readonly BudgetedProvider[],
  places: readonly Place[],
  place: Place,
  signal?: AbortSignal,
): Promise<DiscoverResult> {
  const result: DiscoverResult = { stats: [], inserted: [] };

  for (const provider of providers) {
    const queries = queriesFor(provider, places, place);
    const stats: ProviderStats = {
      provider: provider.name,
      queries: queries.length,
      queriesDone: 0,
      calls: 0,
      found: 0,
      inserted: 0,
      alreadySeen: 0,
      unnamed: 0,
    };
    result.stats.push(stats);

    for (const query of queries) {
      try {
        const found = await provider.search(query, {
          runId,
          signal,
          onReserve: (calls) => (stats.calls += calls),
        });
        const saved = saveSightings(db, runId, found.sightings);
        stats.queriesDone += 1;
        stats.found += found.sightings.length;
        stats.unnamed += found.unnamed;
        stats.inserted += saved.inserted;
        stats.alreadySeen += saved.alreadySeen;
        result.inserted.push(...saved.insertedSightings);
      } catch (error) {
        // Ctrl-C (or a cancelled job) stops the whole run, not just this provider.
        if (signal?.aborted) throw error;
        if (error instanceof BudgetExceededError) stats.budgetStop = error.message;
        else stats.error = error instanceof Error ? error.message : String(error);
        break;
      }
    }
  }
  return result;
}
