import { describe, expect, test } from "vitest";
import { loadConfig } from "../../src/config/load.js";
import { expandPlace, findPlace } from "../../src/config/places.js";
import type { Place } from "../../src/config/schema.js";

const { places } = loadConfig();

function get(query: string): Place {
  const place = findPlace(places, query);
  if (place === undefined) throw new Error(`no place for ${query}`);
  return place;
}

describe("findPlace", () => {
  test.each([
    ["bombay", "mumbai"],
    ["Bombay", "mumbai"],
    ["bangalore", "bengaluru"],
    ["BANGALORE", "bengaluru"],
    ["Madras", "chennai"],
    ["calcutta", "kolkata"],
    ["new delhi", "delhi"],
    ["north goa", "goa"],
  ])("alias %s finds %s", (query, key) => {
    expect(findPlace(places, query)?.key).toBe(key);
  });

  test("matches by key and by name, ignoring case and extra spaces", () => {
    expect(findPlace(places, "mumbai")?.key).toBe("mumbai");
    expect(findPlace(places, "  JAIPUR ")?.key).toBe("jaipur");
    expect(findPlace(places, "  North   Goa ")?.key).toBe("goa");
    expect(findPlace(places, "India")?.type).toBe("country");
  });

  test("returns undefined for an unknown place, a blank, or a partial name", () => {
    expect(findPlace(places, "Atlantis")).toBeUndefined();
    expect(findPlace(places, "")).toBeUndefined();
    expect(findPlace(places, "mum")).toBeUndefined();
  });
});

describe("expandPlace", () => {
  test("a country expands into its cities and regions, not itself", () => {
    const areas = expandPlace(places, get("india"));

    expect(areas).toHaveLength(10);
    expect(areas.map((area) => area.name)).toEqual(expect.arrayContaining(["Goa", "Mumbai"]));
    expect(areas.map((area) => area.name)).not.toContain("India");
    expect(areas.find((area) => area.name === "Goa")?.type).toBe("region");
    expect(areas.find((area) => area.name === "Mumbai")?.type).toBe("city");
  });

  test("a country leaves out other countries' places", () => {
    const lisbon: Place = { ...get("mumbai"), key: "lisbon", name: "Lisbon", aliases: [], country: "Portugal" };

    const names = expandPlace([...places, lisbon], get("india")).map((area) => area.name);

    expect(names).not.toContain("Lisbon");
  });

  test("a region expands into its neighbourhoods", () => {
    const goa = get("goa");
    const areas = expandPlace(places, goa);

    expect(areas.map((area) => area.name)).toEqual(expect.arrayContaining(["Assagao", "Anjuna", "Siolim"]));
    expect(areas.every((area) => area.type === "neighbourhood" && area.place === goa)).toBe(true);
  });

  test("a city expands into its neighbourhoods", () => {
    expect(expandPlace(places, get("bombay")).map((area) => area.name)).toEqual(
      expect.arrayContaining(["Bandra", "Colaba", "BKC", "Kemp's Corner"]),
    );
  });

  test("spelling variants from the legacy catalog appear once", () => {
    const names = expandPlace(places, get("mumbai")).map((area) => area.name);

    expect(names).not.toContain("Kemps Corner");
    expect(names).not.toContain("Santa Cruz");
  });

  test("a place with nothing inside it returns itself", () => {
    const empty: Place = { ...get("pune"), neighbourhoods: [] };
    const country: Place = { ...get("india"), country: "Nowhere" };

    expect(expandPlace(places, empty)).toEqual([{ name: "Pune", type: "city", place: empty }]);
    expect(expandPlace(places, country)).toEqual([{ name: "India", type: "country", place: country }]);
  });
});
