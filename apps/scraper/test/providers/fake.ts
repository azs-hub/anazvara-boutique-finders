import type { SearchArea, Place, ProviderName } from "@anazvara/shared";
import type { Provider, ProviderQuery, QueryKind, Sighting } from "../../src/providers/types.js";

const goa: Place = {
  key: "goa",
  name: "Goa",
  type: "region",
  country: "India",
  bbox: [14.89, 73.67, 15.8, 74.34],
  aliases: [],
  neighbourhoods: ["Assagao"],
  languages: ["en"],
  currency: "INR",
};

export const area: SearchArea = { name: "Assagao", type: "neighbourhood", place: goa };
export const textQuery: ProviderQuery = { kind: "text", text: "concept store", area };
export const areaQuery: ProviderQuery = { kind: "area", area };

export interface FakeProvider extends Provider {
  calls: ProviderQuery[];
}

/** Records each query it receives and returns one sighting per call. */
export function fakeProvider(
  name: ProviderName,
  options: { supports?: QueryKind[]; estimate?: number; fail?: boolean } = {},
): FakeProvider {
  const calls: ProviderQuery[] = [];
  return {
    name,
    supports: options.supports ?? ["text", "area"],
    calls,
    ...(options.estimate !== undefined && { estimateCalls: () => options.estimate! }),
    search(query) {
      calls.push(query);
      if (options.fail) return Promise.reject(new Error("upstream failed"));
      const sighting: Sighting = { source: name, externalId: `${name}-${calls.length}`, placeKey: query.area.place.key };
      return Promise.resolve([sighting]);
    },
  };
}
