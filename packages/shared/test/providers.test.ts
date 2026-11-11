import { describe, expect, test } from "vitest";
import { loadConfig } from "../src/load.js";
import { providerStatuses } from "../src/providers.js";
import type { AppConfig, ProviderName } from "../src/schema.js";

const { app } = loadConfig();
const PAID: ProviderName[] = ["places", "exa", "tavily"];
const ALL_KEYS = { GOOGLE_PLACES_API_KEY: "k", EXA_API_KEY: "k", TAVILY_API_KEY: "k", SEARXNG_URL: "http://x" };

function withAllEnabled(): AppConfig {
  const config = structuredClone(app);
  for (const provider of Object.values(config.providers)) provider.enabled = true;
  return config;
}

function activeNames(config: AppConfig, env: Record<string, string>): ProviderName[] {
  return providerStatuses(config, env)
    .filter((status) => status.active)
    .map((status) => status.name);
}

describe("providerStatuses", () => {
  test("committed config: every provider with a cost is disabled", () => {
    for (const [name, provider] of Object.entries(app.providers)) {
      if (provider.costPerCallUsd > 0) expect([name, provider.enabled]).toEqual([name, false]);
    }
    expect(PAID.every((name) => app.providers[name].costPerCallUsd > 0)).toBe(true);
  });

  test("committed config with no env: only OSM runs", () => {
    expect(activeNames(app, {})).toEqual(["osm"]);
  });

  test("committed config: paid providers stay off even when their keys are set", () => {
    expect(activeNames(app, ALL_KEYS)).toEqual(["osm", "searxng"]);
  });

  test("paid providers enabled but without keys stay off and say why", () => {
    const statuses = providerStatuses(withAllEnabled(), {});

    for (const name of PAID) {
      const status = statuses.find((candidate) => candidate.name === name);
      expect(status?.active).toBe(false);
      expect(status?.reason).toBe(`${app.providers[name].requiredEnv} is not set`);
    }
  });

  test("an empty or blank key counts as not set", () => {
    const config = withAllEnabled();

    expect(activeNames(config, { EXA_API_KEY: "", TAVILY_API_KEY: "   " })).toEqual(["osm"]);
  });

  test("a paid provider runs only when enabled and its key is set", () => {
    expect(activeNames(withAllEnabled(), ALL_KEYS)).toEqual(["osm", "searxng", "places", "exa", "tavily"]);
  });

  test("a disabled provider reports that first", () => {
    const places = providerStatuses(app, {}).find((status) => status.name === "places");

    expect(places?.reason).toBe("disabled in config/app.json");
  });

  test("never exposes a key's value", () => {
    const secret = "super-secret-value";

    const output = JSON.stringify(providerStatuses(withAllEnabled(), { EXA_API_KEY: secret }));

    expect(output).not.toContain(secret);
  });
});
