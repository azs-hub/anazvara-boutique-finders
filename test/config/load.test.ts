import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, test } from "vitest";
import { ConfigError, loadConfig } from "../../src/config/load.js";
import type { AnazvaraProfile, Place } from "../../src/config/schema.js";

const tempDirs: string[] = [];

function configCopy(): string {
  const dir = mkdtempSync(join(tmpdir(), "stockist-config-"));
  tempDirs.push(dir);
  cpSync(new URL("../../config", import.meta.url), dir, { recursive: true });
  return dir;
}

function edit<T>(dir: string, file: string, change: (json: T) => void): void {
  const path = join(dir, file);
  const json = JSON.parse(readFileSync(path, "utf8")) as T;
  change(json);
  writeFileSync(path, JSON.stringify(json));
}

function editFirstCity(dir: string, change: (place: Place) => void): void {
  edit<{ places: Place[] }>(dir, "places.json", ({ places }) => change(places[1]!));
}

function editProfile(dir: string, change: (profile: AnazvaraProfile) => void): void {
  edit(dir, "anazvara-profile.json", change);
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("loadConfig with the committed files", () => {
  const config = loadConfig();

  test("has India, Goa as a region, and the 9 other legacy cities", () => {
    const type = (key: string) => config.places.find((place) => place.key === key)?.type;

    expect(config.places).toHaveLength(11);
    expect(type("india")).toBe("country");
    expect(type("goa")).toBe("region");
    expect(config.places.filter((place) => place.type === "city")).toHaveLength(9);
  });

  test("every place's bbox lies inside India's", () => {
    const [south, west, north, east] = config.places.find((place) => place.key === "india")!.bbox;

    for (const { bbox } of config.places) {
      expect(bbox[0]).toBeGreaterThanOrEqual(south);
      expect(bbox[1]).toBeGreaterThanOrEqual(west);
      expect(bbox[2]).toBeLessThanOrEqual(north);
      expect(bbox[3]).toBeLessThanOrEqual(east);
    }
  });

  test("the India market has the spec's 12 signals and the 5 legacy query families", () => {
    const [market] = config.markets;

    expect(config.markets).toHaveLength(1);
    expect(market?.signals).toHaveLength(12);
    expect(Object.keys(market?.queryVocabulary.en ?? {}).sort()).toEqual([
      "discovery_language",
      "editorial",
      "physical_retail",
      "positioning",
      "retail_identity",
    ]);
  });

  test("the price band is still a placeholder", () => {
    expect(config.profile.priceBands.india).toEqual({ currency: "INR", min: "TODO", max: "TODO" });
  });
});

describe("loadConfig errors", () => {
  test("names the file and the field for an invalid value", () => {
    const dir = configCopy();
    editFirstCity(dir, (place) => {
      place.bbox = [20, 74, 10, 75];
    });

    expect(() => loadConfig(dir)).toThrow(ConfigError);
    expect(() => loadConfig(dir)).toThrow(/places\.json[\s\S]*bbox/);
  });

  test("reports a missing file as ConfigError and names it", () => {
    const dir = configCopy();
    rmSync(join(dir, "app.json"));

    expect(() => loadConfig(dir)).toThrow(ConfigError);
    expect(() => loadConfig(dir)).toThrow(/Cannot read .*app\.json/);
  });

  test("reports broken JSON as ConfigError and names the file", () => {
    const dir = configCopy();
    writeFileSync(join(dir, "markets.json"), "{ not json");

    expect(() => loadConfig(dir)).toThrow(ConfigError);
    expect(() => loadConfig(dir)).toThrow(/Cannot read .*markets\.json/);
  });

  test("rejects a place whose country has no market", () => {
    const dir = configCopy();
    editFirstCity(dir, (place) => {
      place.country = "France";
    });

    expect(() => loadConfig(dir)).toThrow(/no market covers country "France"/);
  });

  test("rejects a place language the market has no vocabulary for", () => {
    const dir = configCopy();
    editFirstCity(dir, (place) => {
      place.languages = ["hi"];
    });

    expect(() => loadConfig(dir)).toThrow(/no "hi" query vocabulary/);
  });

  test("rejects a market with no price band, or one in another currency", () => {
    const missing = configCopy();
    editProfile(missing, (profile) => {
      profile.priceBands = {};
    });
    const wrongCurrency = configCopy();
    editProfile(wrongCurrency, (profile) => {
      profile.priceBands.india!.currency = "EUR";
    });

    expect(() => loadConfig(missing)).toThrow(/no price band for market india/);
    expect(() => loadConfig(wrongCurrency)).toThrow(/in EUR, the market uses INR/);
  });
});
