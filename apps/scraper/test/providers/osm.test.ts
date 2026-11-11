import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { loadConfig } from "@anazvara/shared";
import { openDb, type Db } from "@anazvara/database";
import { BudgetExceededError, BudgetGuard, withBudget } from "../../src/providers/budget.js";
import {
  buildAddress,
  osmFactory,
  overpassQuery,
  retryAfterMs,
  toSighting,
  type OverpassElement,
} from "../../src/providers/osm.js";
import type { ProviderContext } from "../../src/providers/types.js";
import { areaQuery, textQuery } from "./fake.js";
import { ASSAGAO_ELEMENTS, fakeFetch, fixtureFetch, json, testOsm } from "./overpass.js";

const QL = "query";
const free: ProviderContext = { reserve: () => undefined };

describe("overpassQuery", () => {
  test("asks for every matching shop in the bbox, with centers for ways and relations", () => {
    expect(overpassQuery(["clothes", "boutique"], [15.5, 73.7, 15.6, 73.8], 90_000)).toBe(
      ['[out:json][timeout:90];', 'nwr["shop"~"^(clothes|boutique)$"](15.5,73.7,15.6,73.8);', "out center tags;"].join(
        "\n",
      ),
    );
  });

  test("uses the configured shop values", () => {
    const { shopValues } = loadConfig().app.osm;
    expect(overpassQuery(shopValues, [1, 2, 3, 4], 90_000)).toContain(`"^(${shopValues.join("|")})$"`);
  });
});

describe("toSighting", () => {
  test("maps a node from the recorded Assagao response", () => {
    const element = ASSAGAO_ELEMENTS.find((candidate) => (candidate as OverpassElement).id === 13704959781);

    expect(toSighting(element as OverpassElement, "goa", QL)).toEqual({
      source: "osm",
      externalId: "node/13704959781",
      placeKey: "goa",
      query: QL,
      url: "https://www.openstreetmap.org/node/13704959781",
      name: "No Nasties",
      address: "46 Mapusa-Assagao-Anjuna Road, Assagaon, North Goa, 403507",
      lat: 15.5975341,
      lng: 73.7877078,
      phone: "+918792469162",
      website: "https://www.nonasties.in/",
      instagram: undefined,
      raw: (element as OverpassElement).tags,
    });
  });

  test("a way or relation takes its center as the position", () => {
    const way: OverpassElement = { type: "way", id: 42, center: { lat: 15.59, lon: 73.76 }, tags: { name: "Way Shop" } };
    const relation: OverpassElement = { ...way, type: "relation", id: 7 };

    expect(toSighting(way, "goa", QL)).toMatchObject({ externalId: "way/42", lat: 15.59, lng: 73.76 });
    expect(toSighting(relation, "goa", QL)).toMatchObject({
      externalId: "relation/7",
      url: "https://www.openstreetmap.org/relation/7",
    });
  });

  test("missing fields stay undefined and raw keeps every tag", () => {
    const element: OverpassElement = { type: "node", id: 1, lat: 1, lon: 2, tags: { name: " Rang ", shop: "clothes" } };

    expect(toSighting(element, "goa", QL)).toMatchObject({
      name: "Rang",
      address: undefined,
      phone: undefined,
      website: undefined,
      instagram: undefined,
      raw: { name: " Rang ", shop: "clothes" },
    });
  });

  test("falls back to contact:* tags and normalizes Instagram to the handle", () => {
    const element: OverpassElement = {
      type: "node",
      id: 1,
      tags: {
        name: "Shop",
        "contact:phone": "+91 832 000 0000",
        "contact:website": "https://shop.example",
        "contact:instagram": "https://www.instagram.com/The.Shop/?hl=en",
      },
    };

    expect(toSighting(element, "goa", QL)).toMatchObject({
      phone: "+91 832 000 0000",
      website: "https://shop.example",
      instagram: "the.shop",
    });
  });

  test.each<Record<string, string> | undefined>([{ shop: "clothes" }, { name: "  " }, undefined])(
    "an element without a name gives no sighting (%j)",
    (tags) => {
      expect(toSighting({ type: "node", id: 1, ...(tags && { tags }) }, "goa", QL)).toBeUndefined();
    },
  );
});

describe("buildAddress", () => {
  test("prefers addr:full", () => {
    expect(buildAddress({ "addr:full": "Shop 3, Assagao", "addr:street": "Ignored" })).toBe("Shop 3, Assagao");
  });

  test("joins the parts that are present and drops repeats", () => {
    expect(buildAddress({ "addr:street": "Main Road", "addr:place": "Assagao", "addr:city": "assagao" })).toBe(
      "Main Road, Assagao",
    );
    expect(buildAddress({ "addr:floor": "G" })).toBeUndefined();
  });
});

describe("retryAfterMs", () => {
  test("reads seconds or an HTTP date, otherwise the fallback", () => {
    const now = Date.parse("2026-11-11T10:00:00Z");
    expect(retryAfterMs("12", 30_000, now)).toBe(12_000);
    expect(retryAfterMs("Wed, 11 Nov 2026 10:00:05 GMT", 30_000, now)).toBe(5_000);
    expect(retryAfterMs(null, 30_000, now)).toBe(30_000);
    expect(retryAfterMs("soon", 30_000, now)).toBe(30_000);
  });
});

describe("OSM provider", () => {
  test("searches the area's bbox and skips unnamed elements, counting them", async () => {
    const { fetch, requests } = fixtureFetch();
    const { provider } = testOsm(fetch);

    const result = await provider.search(areaQuery, free);

    expect(result.unnamed).toBe(1);
    expect(result.sightings).toHaveLength(ASSAGAO_ELEMENTS.length - 1);
    expect(result.sightings.map((sighting) => sighting.name)).toContain("Bean Me UP");
    expect(result.sightings.every((sighting) => sighting.placeKey === "goa" && sighting.source === "osm")).toBe(true);

    expect(requests).toHaveLength(1);
    const { url, init } = requests[0]!;
    expect(url).toBe("https://overpass.test/api/interpreter");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("user-agent")).toBe("TestAgent/1.0");
    expect(new URLSearchParams(init.body as string).get("data")).toBe(
      overpassQuery(["clothes", "boutique"], areaQuery.area.place.bbox, 90_000),
    );
  });

  test("on 429 waits for Retry-After and retries once, reserving the retry", async () => {
    const { fetch, requests } = fakeFetch(
      () => json("busy", 429, { "Retry-After": "7" }),
      () => json({ elements: [] }),
    );
    const { provider, waits } = testOsm(fetch);
    const reserved: number[] = [];

    const result = await provider.search(areaQuery, { reserve: (calls) => reserved.push(calls) });

    expect(result).toEqual({ sightings: [], unnamed: 0 });
    expect(requests).toHaveLength(2);
    expect(waits).toEqual([7_000]);
    expect(reserved).toEqual([1]);
  });

  test("on 504 without Retry-After waits the configured delay, and gives up after one retry", async () => {
    const { fetch, requests } = fakeFetch(() => json("Gateway Timeout", 504));
    const { provider, waits } = testOsm(fetch);

    await expect(provider.search(areaQuery, free)).rejects.toThrow("Overpass returned HTTP 504: Gateway Timeout");
    expect(requests).toHaveLength(2);
    expect(waits).toEqual([30_000]);
  });

  test("does not retry other errors", async () => {
    const { fetch, requests } = fakeFetch(() => json("bad query", 400));
    const { provider } = testOsm(fetch);

    await expect(provider.search(areaQuery, free)).rejects.toThrow("HTTP 400: bad query");
    expect(requests).toHaveLength(1);
  });

  test("a runtime error remark (Overpass timed out) fails instead of returning partial results", async () => {
    const remark = "runtime error: Query timed out in \"query\" at line 2 after 91 seconds.";
    const { fetch } = fakeFetch(() => json({ elements: [], remark }));

    await expect(testOsm(fetch).provider.search(areaQuery, free)).rejects.toThrow(remark);
  });

  test("an unexpected response shape fails with a readable error", async () => {
    const { fetch } = fakeFetch(() => json({ nope: true }));

    await expect(testOsm(fetch).provider.search(areaQuery, free)).rejects.toThrow("Unexpected Overpass response");
  });

  test("passes the caller's abort signal to fetch, and sends nothing when already aborted", async () => {
    const { fetch, requests } = fixtureFetch();
    const { provider } = testOsm(fetch);
    const controller = new AbortController();

    await provider.search(areaQuery, { ...free, signal: controller.signal });
    controller.abort();

    expect(requests[0]?.init.signal?.aborted).toBe(true);
    await expect(provider.search(areaQuery, { ...free, signal: controller.signal })).rejects.toThrow();
    expect(requests).toHaveLength(1);
  });

  test("sends one request at a time", async () => {
    let active = 0;
    let most = 0;
    const fetchFn = (async () => {
      active += 1;
      most = Math.max(most, active);
      await new Promise((resolve) => setTimeout(resolve, 5));
      active -= 1;
      return json({ elements: [] });
    }) as typeof fetch;
    const { provider } = testOsm(fetchFn);

    await Promise.all([provider.search(areaQuery, free), provider.search(areaQuery, free)]);

    expect(most).toBe(1);
  });

  test("supports area queries only", async () => {
    const { provider } = testOsm(fixtureFetch().fetch);

    expect(provider.supports).toEqual(["area"]);
    await expect(provider.search(textQuery, free)).rejects.toThrow("does not support text queries");
  });

  test("the factory takes the URL, shop values and User-Agent from config", async () => {
    const config = loadConfig();
    const { fetch, requests } = fixtureFetch();
    const original = globalThis.fetch;
    globalThis.fetch = fetch;
    try {
      await osmFactory(config, {}).search(areaQuery, free);
    } finally {
      globalThis.fetch = original;
    }

    expect(requests[0]?.url).toBe(config.app.osm.overpassUrl);
    expect(new Headers(requests[0]?.init.headers).get("user-agent")).toBe(config.app.fetch.userAgent);
  });
});

describe("OSM provider with the budget guard", () => {
  let db: Db;
  let guard: BudgetGuard;

  beforeEach(() => {
    db = openDb(":memory:");
    guard = new BudgetGuard(db, loadConfig().app.providers);
  });

  afterEach(() => {
    db.$client.close();
  });

  test("charges one call per search, and one more for a retry", async () => {
    const ok = withBudget(testOsm(fixtureFetch().fetch).provider, guard);
    await ok.search(areaQuery);
    expect(guard.usage("osm").dailyCalls).toBe(1);

    const busy = fakeFetch(() => json("busy", 429), () => json({ elements: [] }));
    await withBudget(testOsm(busy.fetch).provider, guard).search(areaQuery);
    expect(guard.usage("osm").dailyCalls).toBe(3);
  });

  test("does not retry when the budget has no room for it", async () => {
    const config = loadConfig();
    config.app.providers.osm.dailyLimit = 1;
    const tight = new BudgetGuard(db, config.app.providers);
    const { fetch, requests } = fakeFetch(() => json("busy", 429));
    const { provider, waits } = testOsm(fetch);

    await expect(withBudget(provider, tight).search(areaQuery)).rejects.toBeInstanceOf(BudgetExceededError);
    expect(requests).toHaveLength(1);
    expect(waits).toEqual([]);
  });
});
