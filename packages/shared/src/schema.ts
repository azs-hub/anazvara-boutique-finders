import { z } from "zod";

export const PROVIDER_NAMES = ["osm", "searxng", "places", "exa", "tavily"] as const;
export const PLACE_TYPES = ["city", "region", "country"] as const;

const slug = z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/, "must be a lowercase slug");
const snakeCase = z.string().regex(/^[a-z][a-z0-9_]*$/, "must be snake_case");
const language = z.string().regex(/^[a-z]{2}$/, "must be a two-letter language code");
const currency = z.string().regex(/^[A-Z]{3}$/, "must be a three-letter currency code");
const label = z.string().trim().min(1);
const latitude = z.number().min(-90).max(90);
const longitude = z.number().min(-180).max(180);
const positiveInt = z.number().int().positive();
const score = z.number().min(0).max(100);

/** Case and spacing are ignored when matching place names. */
export function normalizePlaceName(name: string): string {
  return name.trim().replace(/\s+/g, " ").toLowerCase();
}

function repeated(values: readonly string[]): string[] {
  const seen = new Set<string>();
  return values.filter((value) => seen.size === seen.add(value).size);
}

const uniqueStrings = <T extends z.ZodType<string>>(item: T) =>
  z.array(item).refine((values) => repeated(values).length === 0, "must not contain duplicates");

// Order is [south, west, north, east].
export const bboxSchema = z
  .tuple([latitude, longitude, latitude, longitude])
  .refine(
    ([south, west, north, east]) => south < north && west < east,
    "bbox must be [south, west, north, east] with south < north and west < east",
  );

export const placeSchema = z.strictObject({
  key: slug,
  name: label,
  type: z.enum(PLACE_TYPES),
  country: label,
  bbox: bboxSchema,
  aliases: z.array(label),
  neighbourhoods: z.array(label),
  languages: uniqueStrings(language).min(1),
  currency,
});

// findPlace needs every key, name and alias to point at exactly one place.
export const placesFileSchema = z
  .strictObject({ places: z.array(placeSchema).min(1) })
  .superRefine(({ places }, ctx) => {
    const owners = new Map<string, string>();
    places.forEach((place, index) => {
      const names = new Set([place.key, place.name, ...place.aliases].map(normalizePlaceName));
      for (const name of names) {
        const owner = owners.get(name);
        if (owner !== undefined) {
          ctx.addIssue({
            code: "custom",
            message: `"${name}" is used by both ${owner} and ${place.key}`,
            path: ["places", index],
          });
        }
        owners.set(name, place.key);
      }
    });
  });

const queryFamilySchema = z.strictObject({
  // Sampled families run on a few areas only, not as a full sweep of every area.
  sampled: z.boolean(),
  terms: z.array(label).min(1),
});

export const marketSchema = z.strictObject({
  key: slug,
  name: label,
  countries: uniqueStrings(label).min(1),
  currency,
  signals: uniqueStrings(snakeCase).min(1),
  // Language code → family name → terms. A location is appended to each term.
  queryVocabulary: z
    .record(language, z.record(snakeCase, queryFamilySchema))
    .refine((vocabulary) => Object.keys(vocabulary).length > 0, "needs at least one language"),
});

export const marketsFileSchema = z
  .strictObject({ markets: z.array(marketSchema).min(1) })
  .refine(
    ({ markets }) => repeated(markets.map((market) => market.key)).length === 0,
    "market keys must be unique",
  );

const priceBound = z.union([z.number().positive(), z.literal("TODO")]);

const priceBandSchema = z
  .strictObject({ currency, min: priceBound, max: priceBound })
  .refine(
    ({ min, max }) => typeof min !== "number" || typeof max !== "number" || min <= max,
    "min must not exceed max",
  );

export const profileSchema = z.strictObject({
  aestheticSummary: label,
  closestBrands: z.array(label),
  priceBands: z.record(slug, priceBandSchema),
});

const providerSchema = z
  .strictObject({
    enabled: z.boolean(),
    // Environment variable the provider cannot run without (API key or URL).
    requiredEnv: z.string().regex(/^[A-Z][A-Z0-9_]*$/).nullable(),
    costPerCallUsd: z.number().min(0),
    dailyLimit: positiveInt,
    monthlyLimit: positiveInt,
  })
  .refine(({ dailyLimit, monthlyLimit }) => dailyLimit <= monthlyLimit, "dailyLimit must not exceed monthlyLimit")
  .refine(
    ({ costPerCallUsd, requiredEnv }) => costPerCallUsd === 0 || requiredEnv !== null,
    "a paid provider must name the API key it requires in requiredEnv",
  );

const weightsSchema = z
  .strictObject({
    women: z.number().min(0),
    multiBrand: z.number().min(0),
    independent: z.number().min(0),
    aesthetic: z.number().min(0),
    price: z.number().min(0),
    craftNaturalSustainable: z.number().min(0),
    contact: z.number().min(0),
  })
  .refine(
    (weights) => Object.values(weights).reduce((sum, weight) => sum + weight, 0) === 100,
    "scoring weights must add up to 100",
  );

const unitInterval = z.number().min(0).max(1);

export const appSchema = z.strictObject({
  providers: z.record(z.enum(PROVIDER_NAMES), providerSchema),
  fetch: z.strictObject({
    userAgent: label,
    respectRobots: z.literal(true),
    minDelayPerHostMs: z.number().int().min(1000),
    timeoutMs: positiveInt,
    maxBytes: positiveInt,
    maxPagesPerSite: positiveInt,
  }),
  // OpenStreetMap Overpass. The User-Agent is fetch.userAgent (OSM usage policy asks for one).
  osm: z.strictObject({
    overpassUrl: z.url(),
    // Values of the OSM `shop` tag to collect. Nothing else is filtered: later stages decide.
    shopValues: uniqueStrings(z.string().regex(/^[a-z_]+$/, "must be an OSM tag value")).min(1),
    // Also sent to Overpass as the query's [timeout:…], in seconds.
    timeoutMs: z.number().int().min(1000),
    // Wait before the one retry after a 429 or 504 that has no Retry-After header.
    retryDelayMs: z.number().int().min(0),
  }),
  // The model and host come from OLLAMA_MODEL and OLLAMA_HOST in .env.
  llm: z.strictObject({
    provider: z.literal("ollama"),
    temperature: z.number().min(0),
    maxRetries: z.number().int().min(0),
    maxInputTokens: positiveInt,
    timeoutMs: positiveInt,
  }),
  scoring: z.strictObject({
    weights: weightsSchema,
    signalCredit: z.strictObject({ yes: unitInterval, unknown: unitInterval, no: unitInterval }),
    thresholds: z.strictObject({
      priority: z
        .strictObject({ top: score, high: score, medium: score, mediumStoreUnknown: score })
        .refine(({ top, high, medium }) => top > high && high > medium, "needs top > high > medium"),
      fit: z
        .strictObject({ high: score, medium: score })
        .refine(({ high, medium }) => high > medium, "needs high > medium"),
      priceMatchTolerance: z.number().min(0),
    }),
  }),
});

export type Place = z.infer<typeof placeSchema>;
export type PlaceType = (typeof PLACE_TYPES)[number];
export type Market = z.infer<typeof marketSchema>;
export type AnazvaraProfile = z.infer<typeof profileSchema>;
export type AppConfig = z.infer<typeof appSchema>;
export type ProviderName = (typeof PROVIDER_NAMES)[number];

export interface Config {
  app: AppConfig;
  places: Place[];
  markets: Market[];
  profile: AnazvaraProfile;
}
