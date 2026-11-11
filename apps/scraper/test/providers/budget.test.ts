import { afterEach, beforeEach, describe, expect, test } from "vitest";
import type { ProviderName } from "@anazvara/shared";
import { openDb, type Db, apiUsage } from "@anazvara/database";
import { BudgetExceededError, BudgetGuard, withBudget, type ProviderLimits } from "../../src/providers/budget.js";
import { areaQuery, fakeProvider, textQuery } from "./fake.js";

const free: ProviderLimits = { costPerCallUsd: 0, dailyLimit: 1000, monthlyLimit: 30000 };
const LIMITS: Record<ProviderName, ProviderLimits> = {
  osm: free,
  searxng: free,
  places: free,
  tavily: free,
  exa: { costPerCallUsd: 0.007, dailyLimit: 3, monthlyLimit: 5 },
};

let db: Db;
let now: Date;
let guard: BudgetGuard;

beforeEach(() => {
  db = openDb(":memory:");
  now = new Date("2026-11-30T12:00:00.000Z");
  guard = new BudgetGuard(db, LIMITS, () => now);
});

afterEach(() => {
  db.$client.close();
});

const rows = () => db.select().from(apiUsage).all();

function expectRefusal(action: () => unknown, period: string, used: number, limit: number): void {
  try {
    action();
  } catch (error) {
    expect(error).toBeInstanceOf(BudgetExceededError);
    expect(error).toMatchObject({ provider: "exa", period, used, limit });
    return;
  }
  throw new Error("expected BudgetExceededError");
}

describe("BudgetGuard.reserve", () => {
  test("counts calls and cost in one row per provider per day", () => {
    guard.reserve("exa");
    const usage = guard.reserve("exa", 2);

    expect(usage).toMatchObject({ dailyCalls: 3, dailyLimit: 3, monthlyCalls: 3, monthlyLimit: 5 });
    expect(usage.monthlyCostUsd).toBeCloseTo(0.021);
    expect(rows()).toEqual([{ provider: "exa", day: "2026-11-30", calls: 3, costUsd: expect.closeTo(0.021) as number }]);
    expect(guard.usage("exa")).toEqual(usage);
  });

  test("starts at zero and keeps providers apart", () => {
    guard.reserve("osm", 5);

    expect(guard.usage("exa")).toEqual({ dailyCalls: 0, dailyLimit: 3, monthlyCalls: 0, monthlyLimit: 5, monthlyCostUsd: 0 });
    expect(guard.usage("osm")).toMatchObject({ dailyCalls: 5, monthlyCostUsd: 0 });
  });

  test("refuses a reservation past the daily limit and records nothing", () => {
    guard.reserve("exa", 2);

    expectRefusal(() => guard.reserve("exa", 2), "daily", 2, 3);
    expect(guard.usage("exa").dailyCalls).toBe(2);
    expect(rows()).toHaveLength(1);
  });

  test("refuses the first call with nothing recorded when it alone is too big", () => {
    expectRefusal(() => guard.reserve("exa", 4), "daily", 0, 3);
    expect(rows()).toEqual([]);
  });

  test("the next UTC day resets the daily count but keeps the monthly one", () => {
    now = new Date("2026-11-29T12:00:00.000Z");
    guard.reserve("exa", 3);
    now = new Date("2026-11-29T23:59:59.999Z");
    expectRefusal(() => guard.reserve("exa"), "daily", 3, 3);

    now = new Date("2026-11-30T00:00:00.000Z");

    expect(guard.reserve("exa", 2)).toMatchObject({ dailyCalls: 2, monthlyCalls: 5 });
    expect(rows().map((row) => [row.day, row.calls])).toEqual([
      ["2026-11-29", 3],
      ["2026-11-30", 2],
    ]);
  });

  test("refuses a reservation past the monthly limit even on a fresh day", () => {
    now = new Date("2026-11-01T08:00:00.000Z");
    guard.reserve("exa", 3);
    now = new Date("2026-11-02T08:00:00.000Z");
    guard.reserve("exa", 2);
    now = new Date("2026-11-03T08:00:00.000Z");

    expectRefusal(() => guard.reserve("exa"), "monthly", 5, 5);
    expect(guard.usage("exa")).toMatchObject({ dailyCalls: 0, monthlyCalls: 5 });
    expect(rows()).toHaveLength(2);
  });

  test("a new month starts fresh", () => {
    now = new Date("2026-11-29T08:00:00.000Z");
    guard.reserve("exa", 3);
    now = new Date("2026-11-30T08:00:00.000Z");
    guard.reserve("exa", 2);
    now = new Date("2026-12-01T00:00:00.000Z");

    expect(guard.usage("exa")).toEqual({ dailyCalls: 0, dailyLimit: 3, monthlyCalls: 0, monthlyLimit: 5, monthlyCostUsd: 0 });
    expect(guard.reserve("exa", 3)).toMatchObject({ dailyCalls: 3, monthlyCalls: 3 });
  });

  test("uses UTC days, not local ones", () => {
    // 23:30 UTC is already the next day in India (UTC+5:30).
    now = new Date("2026-11-30T23:30:00.000Z");
    guard.reserve("exa");

    expect(rows()[0]?.day).toBe("2026-11-30");
  });

  test.each([-1, 1.5, Number.NaN])("rejects %s calls without recording", (calls) => {
    expect(() => guard.reserve("exa", calls)).toThrow(RangeError);
    expect(rows()).toEqual([]);
  });

  test("zero calls checks the limits but records nothing", () => {
    expect(guard.reserve("exa", 0)).toMatchObject({ dailyCalls: 0 });
    expect(rows()).toEqual([]);
  });
});

describe("withBudget", () => {
  test("reserves before each search and passes the result through", async () => {
    const inner = fakeProvider("exa");
    const provider = withBudget(inner, guard);

    const sightings = await provider.search(textQuery, {});

    expect(sightings).toEqual([{ source: "exa", externalId: "exa-1", placeKey: "goa" }]);
    expect(guard.usage("exa").dailyCalls).toBe(1);
  });

  test("blocks the inner search once over budget", async () => {
    const inner = fakeProvider("exa");
    const provider = withBudget(inner, guard);
    for (let i = 0; i < 3; i++) await provider.search(textQuery, {});

    await expect(provider.search(textQuery, {})).rejects.toBeInstanceOf(BudgetExceededError);
    expect(inner.calls).toHaveLength(3);
  });

  test("reserves the provider's estimate and blocks a search that would pass the limit", async () => {
    const inner = fakeProvider("exa", { estimate: 2 });
    const provider = withBudget(inner, guard);

    await provider.search(textQuery, {});
    await expect(provider.search(textQuery, {})).rejects.toBeInstanceOf(BudgetExceededError);

    expect(inner.calls).toHaveLength(1);
    expect(guard.usage("exa").dailyCalls).toBe(2);
    expect(provider.estimateCalls?.(textQuery)).toBe(2);
  });

  test("a failed request still counts", async () => {
    const provider = withBudget(fakeProvider("exa", { fail: true }), guard);

    await expect(provider.search(textQuery, {})).rejects.toThrow("upstream failed");
    expect(guard.usage("exa").dailyCalls).toBe(1);
  });

  test("an unsupported query kind or an aborted search spends nothing", async () => {
    const inner = fakeProvider("exa", { supports: ["text"] });
    const provider = withBudget(inner, guard);

    await expect(provider.search(areaQuery, {})).rejects.toThrow("does not support area queries");
    await expect(provider.search(textQuery, { signal: AbortSignal.abort() })).rejects.toThrow();
    expect(inner.calls).toEqual([]);
    expect(rows()).toEqual([]);
  });

  test("keeps the provider's name and supported kinds", () => {
    const provider = withBudget(fakeProvider("exa", { supports: ["text"] }), guard);

    expect(provider).toMatchObject({ name: "exa", supports: ["text"] });
    expect("estimateCalls" in provider).toBe(false);
  });
});
