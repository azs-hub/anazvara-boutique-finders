import { normalizePlaceName, type Place, type PlaceType } from "./schema.js";

export interface SearchArea {
  name: string;
  type: PlaceType | "neighbourhood";
  /** The catalog entry holding this area's bbox, languages and currency. */
  place: Place;
}

/** Finds a place by key, name or alias, ignoring case and extra spaces. */
export function findPlace(places: readonly Place[], query: string): Place | undefined {
  const wanted = normalizePlaceName(query);
  return places.find((place) =>
    [place.key, place.name, ...place.aliases].some((name) => normalizePlaceName(name) === wanted),
  );
}

function asArea(place: Place): SearchArea {
  return { name: place.name, type: place.type, place };
}

/**
 * The areas inside a place: a country's cities and regions, or a city's or
 * region's neighbourhoods. A place with nothing inside it returns itself, so
 * the result is never empty. Otherwise the place itself is not included.
 */
export function expandPlace(places: readonly Place[], place: Place): SearchArea[] {
  const areas: SearchArea[] =
    place.type === "country"
      ? places.filter((other) => other.type !== "country" && other.country === place.country).map(asArea)
      : place.neighbourhoods.map((name) => ({ name, type: "neighbourhood", place }));

  return areas.length > 0 ? areas : [asArea(place)];
}
