import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { loadConfig } from "../../src/config/load.js";
import type { Config, ProviderName } from "../../src/config/schema.js";
import { openDb, type Db } from "../../src/db/client.js";
import { BudgetExceededError } from "../../src/providers/budget.js";
import { buildProviders, PROVIDER_FACTORIES, type ProviderFactories } from "../../src/providers/registry.js";
import { fakeProvider, textQuery, type FakeProvider } from "./fake.js";

const ALL_ENV = { GOOGLE_PLACES_API_KEY: "k", EXA_API_KEY: "k", TAVILY_API_KEY: "k", SEARXNG_URL: "http://x" };

let db: Db;
let built: FakeProvider[];

beforeEach(() => {
  db = openDb(":memory:");
  built = [];
});

afterEach(() => {
  db.$client.close();
});

function allEnabled(): Config {
  const config = structuredClone(loadConfig());
  for (const provider of Object.values(config.app.providers)) provider.enabled = true;
  return config;
}

function fakes(...names: ProviderName[]): ProviderFactories {
  return Object.fromEntries(
    names.map((name) => [
      name,
      () => {
        const provider = fakeProvider(name);
        built.push(provider);
        return provider;
      },
    ]),
  );
}

describe("buildProviders", () => {
  test("no providers are registered yet", () => {
    expect(PROVIDER_FACTORIES).toEqual({});
    expect(buildProviders(allEnabled(), ALL_ENV, db).skipped.map((s) => s.reason)).toEqual(
      Array(5).fill("not implemented yet"),
    );
  });

  test("skips with the right reason: disabled, missing env, or not implemented", () => {
    const { providers, skipped } = buildProviders(loadConfig(), { EXA_API_KEY: "k" }, db, fakes("osm", "exa"));

    expect(providers.map((provider) => provider.name)).toEqual(["osm"]);
    expect(skipped).toEqual([
      { name: "searxng", reason: "SEARXNG_URL is not set" },
      { name: "places", reason: "disabled in config/app.json" },
      { name: "exa", reason: "disabled in config/app.json" },
      { name: "tavily", reason: "disabled in config/app.json" },
    ]);
  });

  test("an enabled provider without its key is skipped, not built", () => {
    const { providers, skipped } = buildProviders(allEnabled(), {}, db, fakes("exa"));

    expect(providers).toEqual([]);
    expect(skipped).toContainEqual({ name: "exa", reason: "EXA_API_KEY is not set" });
    expect(built).toEqual([]);
  });

  test("an active provider with no factory is reported as not implemented", () => {
    const { skipped } = buildProviders(allEnabled(), ALL_ENV, db, fakes("osm"));

    expect(skipped).toContainEqual({ name: "tavily", reason: "not implemented yet" });
  });

  test("`only` leaves the other providers out entirely", () => {
    const { providers, skipped } = buildProviders(allEnabled(), ALL_ENV, db, fakes("osm", "exa"), ["exa", "tavily"]);

    expect(providers.map((provider) => provider.name)).toEqual(["exa"]);
    expect(skipped).toEqual([{ name: "tavily", reason: "not implemented yet" }]);
  });

  test("returned providers are wrapped with the budget guard", async () => {
    const config = allEnabled();
    config.app.providers.exa.dailyLimit = 1;
    const {
      providers: [exa],
    } = buildProviders(config, ALL_ENV, db, fakes("exa"));

    await exa!.search(textQuery, {});
    await expect(exa!.search(textQuery, {})).rejects.toBeInstanceOf(BudgetExceededError);
    expect(built[0]?.calls).toHaveLength(1);
    expect(exa).not.toBe(built[0]);
  });

  test("rejects a factory that builds a provider under another name", () => {
    const factories: ProviderFactories = { osm: () => fakeProvider("exa") };

    expect(() => buildProviders(allEnabled(), ALL_ENV, db, factories)).toThrow(/Factory for osm built a provider named exa/);
  });
});
