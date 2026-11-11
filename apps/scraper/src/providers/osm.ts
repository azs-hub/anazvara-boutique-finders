import { setTimeout as delay } from "node:timers/promises";
import { z } from "zod";
import type { Place } from "@anazvara/shared";
import { clean, instagramHandle } from "./normalize.js";
import type { ProviderFactory } from "./registry.js";
import type { Provider, ProviderContext, Sighting } from "./types.js";

/** OpenStreetMap through the Overpass API: every shop with a matching `shop` tag in a bbox. */

export interface OsmOptions {
  overpassUrl: string;
  shopValues: readonly string[];
  timeoutMs: number;
  retryDelayMs: number;
  userAgent: string;
  fetch?: typeof fetch;
  /** Waits before a retry. Injected in tests. */
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  /** For Retry-After dates. */
  now?: () => number;
}

const tagsSchema = z.record(z.string(), z.string());
const elementSchema = z.object({
  type: z.enum(["node", "way", "relation"]),
  id: z.number().int(),
  lat: z.number().optional(),
  lon: z.number().optional(),
  center: z.object({ lat: z.number(), lon: z.number() }).optional(),
  tags: tagsSchema.optional(),
});
const responseSchema = z.object({ elements: z.array(elementSchema), remark: z.string().optional() });

export type OverpassElement = z.infer<typeof elementSchema>;
type Tags = z.infer<typeof tagsSchema>;

// Statuses Overpass uses when it is busy: too many requests from us, or a gateway timeout.
const RETRY_STATUSES = new Set([429, 504]);

/** The Overpass QL for one bbox. Overpass wants (south, west, north, east), the same order as places.json. */
export function overpassQuery(shopValues: readonly string[], bbox: Place["bbox"], timeoutMs: number): string {
  const seconds = Math.ceil(timeoutMs / 1000);
  return [
    `[out:json][timeout:${seconds}];`,
    `nwr["shop"~"^(${shopValues.join("|")})$"](${bbox.join(",")});`,
    "out center tags;",
  ].join("\n");
}

/** One address line from addr:* tags: addr:full if set, otherwise the parts that are present. */
export function buildAddress(tags: Tags): string | undefined {
  const full = clean(tags["addr:full"]);
  if (full) return full;

  const street = [tags["addr:housenumber"], tags["addr:street"]].map(clean).filter(Boolean).join(" ");
  const parts = [
    tags["addr:housename"],
    street,
    tags["addr:place"],
    tags["addr:suburb"],
    tags["addr:city"],
    tags["addr:district"],
    tags["addr:state"],
    tags["addr:postcode"],
  ]
    .map(clean)
    .filter((part): part is string => part !== undefined);

  // OSM often repeats a value, e.g. the same name as addr:place and addr:city.
  const unique = parts.filter(
    (part, index) => parts.findIndex((other) => other.toLowerCase() === part.toLowerCase()) === index,
  );
  return unique.length > 0 ? unique.join(", ") : undefined;
}

const firstOf = (tags: Tags, ...keys: string[]) =>
  keys.map((key) => clean(tags[key])).find((value) => value !== undefined);

/** Maps one element to a sighting, or undefined if it has no name. Nothing else is filtered. */
export function toSighting(element: OverpassElement, placeKey: string, query: string): Sighting | undefined {
  const tags = element.tags ?? {};
  const name = clean(tags.name);
  if (name === undefined) return undefined;

  // Nodes have their own coordinates; ways and relations have a center from `out center`.
  const point = element.type === "node" ? { lat: element.lat, lon: element.lon } : element.center;
  const externalId = `${element.type}/${element.id}`;

  return {
    source: "osm",
    externalId,
    placeKey,
    query,
    url: `https://www.openstreetmap.org/${externalId}`,
    name,
    address: buildAddress(tags),
    lat: point?.lat,
    lng: point?.lon,
    phone: firstOf(tags, "phone", "contact:phone", "mobile", "contact:mobile"),
    website: firstOf(tags, "website", "contact:website"),
    instagram: instagramHandle(tags["contact:instagram"]),
    raw: tags,
  };
}

/** Milliseconds to wait from a Retry-After header (seconds or an HTTP date), or the fallback. */
export function retryAfterMs(header: string | null, fallbackMs: number, now: number = Date.now()): number {
  const value = header?.trim();
  if (!value) return fallbackMs;
  if (/^\d+$/.test(value)) return Number(value) * 1000;
  const date = Date.parse(value);
  return Number.isNaN(date) ? fallbackMs : Math.max(0, date - now);
}

export class OverpassError extends Error {
  override name = "OverpassError";
}

const abortableSleep = (ms: number, signal?: AbortSignal) => delay(ms, undefined, { signal });

export function createOsmProvider(options: OsmOptions): Provider {
  const fetchFn = options.fetch ?? fetch;
  const sleep = options.sleep ?? abortableSleep;
  const now = options.now ?? Date.now;
  // OSM usage policy: one request at a time, even if a caller runs searches in parallel.
  let queue: Promise<unknown> = Promise.resolve();

  function post(query: string, ctx: ProviderContext): Promise<Response> {
    const timeout = AbortSignal.timeout(options.timeoutMs);
    return fetchFn(options.overpassUrl, {
      method: "POST",
      headers: {
        "User-Agent": options.userAgent,
        Accept: "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({ data: query }).toString(),
      signal: ctx.signal ? AbortSignal.any([ctx.signal, timeout]) : timeout,
    });
  }

  async function fetchElements(query: string, ctx: ProviderContext): Promise<OverpassElement[]> {
    let response = await post(query, ctx);
    if (RETRY_STATUSES.has(response.status)) {
      const wait = retryAfterMs(response.headers.get("retry-after"), options.retryDelayMs, now());
      await response.body?.cancel();
      // The retry is a second request, so it is reserved like any other.
      ctx.reserve(1);
      await sleep(wait, ctx.signal);
      response = await post(query, ctx);
    }
    if (!response.ok) {
      const body = (await response.text()).slice(0, 200).trim();
      throw new OverpassError(`Overpass returned HTTP ${response.status}${body ? `: ${body}` : ""}`);
    }

    const parsed = responseSchema.safeParse(await response.json());
    if (!parsed.success) {
      throw new OverpassError(`Unexpected Overpass response: ${z.prettifyError(parsed.error)}`);
    }
    // A query that times out inside Overpass still returns 200, with partial results and a remark.
    if (parsed.data.remark?.includes("error")) {
      throw new OverpassError(`Overpass: ${parsed.data.remark}`);
    }
    return parsed.data.elements;
  }

  return {
    name: "osm",
    supports: ["area"],
    estimateCalls: () => 1,
    search(query, ctx) {
      const run = async () => {
        if (query.kind !== "area") throw new Error(`osm does not support ${query.kind} queries`);
        ctx.signal?.throwIfAborted();
        const placeKey = query.area.place.key;
        const ql = overpassQuery(options.shopValues, query.area.place.bbox, options.timeoutMs);

        const elements = await fetchElements(ql, ctx);
        const sightings = elements.flatMap((element) => toSighting(element, placeKey, ql) ?? []);
        return { sightings, unnamed: elements.length - sightings.length };
      };
      const result = queue.then(run, run);
      queue = result.catch(() => undefined);
      return result;
    },
  };
}

export const osmFactory: ProviderFactory = (config) =>
  createOsmProvider({ ...config.app.osm, userAgent: config.app.fetch.userAgent });
