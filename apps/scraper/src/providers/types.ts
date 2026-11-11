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

/** What a caller passes to a budgeted provider (see withBudget). */
export interface SearchContext {
  runId?: number;
  signal?: AbortSignal;
  /** Told about every reservation that succeeds, so the caller can count calls. */
  onReserve?: (calls: number) => void;
}

/** What a provider receives. withBudget builds it. */
export interface ProviderContext {
  runId?: number;
  signal?: AbortSignal;
  /**
   * Reserves `calls` more against the budget. The first request is reserved
   * already (estimateCalls); call this before each extra page or retry.
   * Throws BudgetExceededError, and then the request must not be sent.
   */
  reserve(calls: number): void;
}

export interface SearchResult {
  sightings: Sighting[];
  /** Results the provider dropped because they have no name. */
  unnamed: number;
}

interface ProviderInfo {
  readonly name: ProviderName;
  readonly supports: readonly QueryKind[];
  /** Billable calls one search will make, reserved before it starts. Defaults to 1. */
  estimateCalls?(query: ProviderQuery): number;
}

export interface Provider extends ProviderInfo {
  search(query: ProviderQuery, ctx: ProviderContext): Promise<SearchResult>;
}

/** A provider wrapped by withBudget: every request it sends is reserved first. */
export interface BudgetedProvider extends ProviderInfo {
  search(query: ProviderQuery, ctx?: SearchContext): Promise<SearchResult>;
}
