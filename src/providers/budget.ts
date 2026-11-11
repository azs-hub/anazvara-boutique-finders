import { and, eq, like, sql } from "drizzle-orm";
import type { AppConfig, ProviderName } from "../config/schema.js";
import type { Db } from "../db/client.js";
import { apiUsage } from "../db/schema.js";
import type { Provider } from "./types.js";

export type ProviderLimits = Pick<AppConfig["providers"][ProviderName], "costPerCallUsd" | "dailyLimit" | "monthlyLimit">;
export type BudgetPeriod = "daily" | "monthly";

export interface Usage {
  dailyCalls: number;
  dailyLimit: number;
  monthlyCalls: number;
  monthlyLimit: number;
  monthlyCostUsd: number;
}

export class BudgetExceededError extends Error {
  override name = "BudgetExceededError";

  constructor(
    readonly provider: ProviderName,
    readonly period: BudgetPeriod,
    readonly used: number,
    readonly limit: number,
  ) {
    super(`${provider}: ${period} limit reached (${used} of ${limit} calls used)`);
  }
}

// Day keys are UTC, so a run near midnight never counts against two local days.
const dayKey = (date: Date) => date.toISOString().slice(0, 10);
const monthKey = (date: Date) => date.toISOString().slice(0, 7);

type Reader = Pick<Db, "select">;

/** Counts paid calls per provider per day in api_usage and refuses calls past the limits. */
export class BudgetGuard {
  constructor(
    private readonly db: Db,
    private readonly limits: Readonly<Record<ProviderName, ProviderLimits>>,
    private readonly clock: () => Date = () => new Date(),
  ) {}

  usage(provider: ProviderName): Usage {
    return this.read(this.db, provider, this.clock());
  }

  /**
   * Records `calls` before they are made. Throws BudgetExceededError, and
   * records nothing, if they would pass the daily or monthly limit.
   */
  reserve(provider: ProviderName, calls = 1): Usage {
    if (!Number.isInteger(calls) || calls < 0) {
      throw new RangeError(`${provider}: calls must be a whole number ≥ 0, got ${calls}`);
    }
    const now = this.clock();
    const { costPerCallUsd } = this.limits[provider];

    // Immediate: takes the write lock first, so two processes can't both pass the check.
    return this.db.transaction(
      (tx) => {
        const before = this.read(tx, provider, now);
        if (before.dailyCalls + calls > before.dailyLimit) {
          throw new BudgetExceededError(provider, "daily", before.dailyCalls, before.dailyLimit);
        }
        if (before.monthlyCalls + calls > before.monthlyLimit) {
          throw new BudgetExceededError(provider, "monthly", before.monthlyCalls, before.monthlyLimit);
        }
        if (calls === 0) return before;

        const cost = calls * costPerCallUsd;
        tx.insert(apiUsage)
          .values({ provider, day: dayKey(now), calls, costUsd: cost })
          .onConflictDoUpdate({
            target: [apiUsage.provider, apiUsage.day],
            set: { calls: sql`${apiUsage.calls} + ${calls}`, costUsd: sql`${apiUsage.costUsd} + ${cost}` },
          })
          .run();
        return {
          ...before,
          dailyCalls: before.dailyCalls + calls,
          monthlyCalls: before.monthlyCalls + calls,
          monthlyCostUsd: before.monthlyCostUsd + cost,
        };
      },
      { behavior: "immediate" },
    );
  }

  private read(db: Reader, provider: ProviderName, now: Date): Usage {
    const { dailyLimit, monthlyLimit } = this.limits[provider];
    const sumWhere = (dayPattern: string) =>
      db
        .select({
          calls: sql<number>`coalesce(sum(${apiUsage.calls}), 0)`,
          costUsd: sql<number>`coalesce(sum(${apiUsage.costUsd}), 0)`,
        })
        .from(apiUsage)
        .where(and(eq(apiUsage.provider, provider), like(apiUsage.day, dayPattern)))
        .get() ?? { calls: 0, costUsd: 0 };

    const day = sumWhere(dayKey(now));
    const month = sumWhere(`${monthKey(now)}-%`);
    return {
      dailyCalls: day.calls,
      dailyLimit,
      monthlyCalls: month.calls,
      monthlyLimit,
      monthlyCostUsd: month.costUsd,
    };
  }
}

/**
 * Wraps a provider so every search reserves its calls first. A request that
 * fails after reserving still counts: over-counting is the safe direction.
 */
export function withBudget(provider: Provider, guard: BudgetGuard): Provider {
  return {
    name: provider.name,
    supports: provider.supports,
    ...(provider.estimateCalls && { estimateCalls: provider.estimateCalls.bind(provider) }),
    // Async so every refusal reaches the caller as a rejected promise.
    async search(query, ctx) {
      // Check what costs nothing before spending budget.
      ctx.signal?.throwIfAborted();
      if (!provider.supports.includes(query.kind)) {
        throw new Error(`${provider.name} does not support ${query.kind} queries`);
      }
      guard.reserve(provider.name, provider.estimateCalls?.(query) ?? 1);
      return provider.search(query, ctx);
    },
  };
}
