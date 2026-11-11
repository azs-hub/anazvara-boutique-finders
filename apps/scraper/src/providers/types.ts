import type { SearchArea, ProviderName } from "@anazvara/shared";

export type SightingSource = ProviderName | "guide" | "manual";

/**
 * One raw find from one source, saved as is before matching. Providers only
 * collect: deciding whether a sighting is a stockist happens in later stages.
 */
export interface Sighting {
  source: SightingSource;
  /** Stable id within the source: OSM element id, place id, or normalized URL. */
  externalId: string;
  /** Key of the catalog place that was searched. */
  placeKey: string;
  query?: string;
  name?: string;
  url?: string;
  title?: string;
  snippet?: string;
  address?: string;
  lat?: number;
  lng?: number;
  phone?: string;
  website?: string;
  instagram?: string;
  raw?: unknown;
}

export type ProviderQuery =
  | { kind: "text"; text: string; area: SearchArea }
  | { kind: "area"; area: SearchArea };

export type QueryKind = ProviderQuery["kind"];

export interface ProviderContext {
  runId?: number;
  signal?: AbortSignal;
}

export interface Provider {
  readonly name: ProviderName;
  readonly supports: readonly QueryKind[];
  /** Billable calls one search will make. Defaults to 1. */
  estimateCalls?(query: ProviderQuery): number;
  search(query: ProviderQuery, ctx: ProviderContext): Promise<Sighting[]>;
}
