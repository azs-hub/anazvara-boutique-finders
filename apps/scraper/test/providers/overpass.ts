import { readFileSync } from "node:fs";
import { createOsmProvider, type OsmOptions } from "../../src/providers/osm.js";
import type { Provider } from "../../src/providers/types.js";

/** A real Overpass response for a small bbox around Assagao, Goa (recorded 11 Nov 2026). */
export const ASSAGAO_FIXTURE = readFileSync(new URL("../fixtures/osm-assagao.json", import.meta.url), "utf8");
export const ASSAGAO_ELEMENTS = (JSON.parse(ASSAGAO_FIXTURE) as { elements: unknown[] }).elements;

export interface RecordedRequest {
  url: string;
  init: RequestInit;
}

export interface FakeFetch {
  fetch: typeof fetch;
  requests: RecordedRequest[];
}

export const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

/** Answers each request with the next response; the last one repeats. */
export function fakeFetch(...responses: (() => Response)[]): FakeFetch {
  const requests: RecordedRequest[] = [];
  const fetchFn: typeof fetch = (input, init = {}) => {
    const url = input instanceof Request ? input.url : input.toString();
    requests.push({ url, init });
    const next = responses[Math.min(requests.length - 1, responses.length - 1)];
    return Promise.resolve(next!());
  };
  return { fetch: fetchFn, requests };
}

export const fixtureFetch = () => fakeFetch(() => json(ASSAGAO_FIXTURE));

export interface TestOsm {
  provider: Provider;
  waits: number[];
}

/** An OSM provider with test settings; sleeping only records how long it would wait. */
export function testOsm(fetchFn: typeof fetch, options: Partial<OsmOptions> = {}): TestOsm {
  const waits: number[] = [];
  const provider = createOsmProvider({
    overpassUrl: "https://overpass.test/api/interpreter",
    shopValues: ["clothes", "boutique"],
    timeoutMs: 90_000,
    retryDelayMs: 30_000,
    userAgent: "TestAgent/1.0",
    fetch: fetchFn,
    sleep: (ms) => {
      waits.push(ms);
      return Promise.resolve();
    },
    ...options,
  });
  return { provider, waits };
}
