import { describe, expect, test } from "vitest";
import {
  appSchema,
  bboxSchema,
  marketsFileSchema,
  placesFileSchema,
  profileSchema,
  type AppConfig,
  type Place,
} from "../src/schema.js";
import { loadConfig } from "../src/load.js";

const place: Place = {
  key: "lisbon",
  name: "Lisbon",
  type: "city",
  country: "Portugal",
  bbox: [38.69, -9.23, 38.8, -9.09],
  aliases: ["Lisboa"],
  neighbourhoods: ["Chiado"],
  languages: ["pt", "en"],
  currency: "EUR",
};

describe("bboxSchema", () => {
  test("accepts [south, west, north, east], including negative longitudes", () => {
    expect(bboxSchema.safeParse(place.bbox).success).toBe(true);
  });

  test.each([
    ["south above north", [19.27, 72.77, 18.89, 72.99]],
    ["west above east", [18.89, 72.99, 19.27, 72.77]],
    ["latitude out of range", [-91, 72.77, 19.27, 72.99]],
    ["longitude out of range", [18.89, 72.77, 19.27, 181]],
    ["too few values", [18.89, 72.77, 19.27]],
  ])("rejects %s", (_name, bbox) => {
    expect(bboxSchema.safeParse(bbox).success).toBe(false);
  });
});

describe("placesFileSchema", () => {
  test("accepts a place outside India", () => {
    expect(placesFileSchema.safeParse({ places: [place] }).success).toBe(true);
  });

  test("rejects an alias that points at two places", () => {
    const other = { ...place, key: "other", name: "Other", aliases: ["LISBOA"] };

    expect(placesFileSchema.safeParse({ places: [place, other] }).success).toBe(false);
  });

  test("rejects unknown fields, so typos don't pass silently", () => {
    const places = [{ ...place, neighborhoods: [] }];

    expect(placesFileSchema.safeParse({ places }).success).toBe(false);
  });
});

describe("marketsFileSchema", () => {
  const market = {
    key: "france",
    name: "France",
    countries: ["France"],
    currency: "EUR",
    signals: ["boho"],
    queryVocabulary: { fr: { retail: { sampled: false, terms: ["boutique multimarques"] } } },
  };

  test("accepts a second market with its own language", () => {
    expect(marketsFileSchema.safeParse({ markets: [market] }).success).toBe(true);
  });

  test("rejects duplicate market keys and duplicate signals", () => {
    expect(marketsFileSchema.safeParse({ markets: [market, market] }).success).toBe(false);
    expect(marketsFileSchema.safeParse({ markets: [{ ...market, signals: ["a", "a"] }] }).success).toBe(false);
  });

  test("rejects a market with no vocabulary", () => {
    expect(marketsFileSchema.safeParse({ markets: [{ ...market, queryVocabulary: {} }] }).success).toBe(false);
  });
});

describe("profileSchema", () => {
  const profile = (min: unknown, max: unknown) => ({
    aestheticSummary: "x",
    closestBrands: [],
    priceBands: { india: { currency: "INR", min, max } },
  });

  test("accepts a TODO band or a real one", () => {
    expect(profileSchema.safeParse(profile("TODO", "TODO")).success).toBe(true);
    expect(profileSchema.safeParse(profile(2000, 5000)).success).toBe(true);
  });

  test("rejects an inverted band or other placeholder text", () => {
    expect(profileSchema.safeParse(profile(5000, 2000)).success).toBe(false);
    expect(profileSchema.safeParse(profile("tbd", 5000)).success).toBe(false);
  });
});

describe("appSchema", () => {
  const app = (): AppConfig => structuredClone(loadConfig().app);

  test("rejects weights that don't add up to 100", () => {
    const config = app();
    config.scoring.weights.women = 25;

    expect(appSchema.safeParse(config).success).toBe(false);
  });

  test("rejects a paid provider with no required key", () => {
    const config = app();
    config.providers.exa.requiredEnv = null;

    expect(appSchema.safeParse(config).success).toBe(false);
  });

  test("rejects a missing provider", () => {
    const providers: Partial<AppConfig["providers"]> = app().providers;
    delete providers.osm;

    expect(appSchema.safeParse({ ...app(), providers }).success).toBe(false);
  });

  test("rejects fetching faster than 1 request per second per host", () => {
    const config = app();
    config.fetch.minDelayPerHostMs = 500;

    expect(appSchema.safeParse(config).success).toBe(false);
  });

  test("rejects an invalid Overpass URL or shop value", () => {
    const badUrl = app();
    badUrl.osm.overpassUrl = "not a url";
    const badValue = app();
    badValue.osm.shopValues = ["clothes", "x|y"];

    expect(appSchema.safeParse(badUrl).success).toBe(false);
    expect(appSchema.safeParse(badValue).success).toBe(false);
  });
});
