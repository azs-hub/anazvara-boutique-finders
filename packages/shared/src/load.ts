import { readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import {
  appSchema,
  marketsFileSchema,
  placesFileSchema,
  profileSchema,
  type Config,
} from "./schema.js";
import { CONFIG_DIR } from "./paths.js";

/** Invalid configuration. The message is meant to be shown to the user as is. */
export class ConfigError extends Error {
  override name = "ConfigError";
}

function readJson(path: string): unknown {
  try {
    return JSON.parse(readFileSync(path, "utf8")) as unknown;
  } catch (error) {
    throw new ConfigError(`Cannot read ${path}: ${(error as Error).message}`, { cause: error });
  }
}

function parseFile<S extends z.ZodType>(schema: S, path: string): z.output<S> {
  const result = schema.safeParse(readJson(path));
  if (!result.success) {
    throw new ConfigError(`Invalid ${path}:\n${z.prettifyError(result.error)}`);
  }
  return result.data;
}

/** Problems no single file can detect: references between the four files. */
export function crossFileProblems({ places, markets, profile }: Config): string[] {
  const problems: string[] = [];

  for (const place of places) {
    const market = markets.find((candidate) => candidate.countries.includes(place.country));
    if (market === undefined) {
      problems.push(`places.json: ${place.key}: no market covers country "${place.country}"`);
      continue;
    }
    for (const language of place.languages) {
      if (market.queryVocabulary[language] === undefined) {
        problems.push(`places.json: ${place.key}: market ${market.key} has no "${language}" query vocabulary`);
      }
    }
  }

  for (const market of markets) {
    const band = profile.priceBands[market.key];
    if (band === undefined) {
      problems.push(`anazvara-profile.json: no price band for market ${market.key}`);
    } else if (band.currency !== market.currency) {
      problems.push(`anazvara-profile.json: price band for ${market.key} is in ${band.currency}, the market uses ${market.currency}`);
    }
  }

  return problems;
}

/** Loads and validates all configuration. Throws ConfigError if anything is invalid. */
export function loadConfig(dir: string = CONFIG_DIR): Config {
  const config: Config = {
    app: parseFile(appSchema, join(dir, "app.json")),
    places: parseFile(placesFileSchema, join(dir, "places.json")).places,
    markets: parseFile(marketsFileSchema, join(dir, "markets.json")).markets,
    profile: parseFile(profileSchema, join(dir, "anazvara-profile.json")),
  };

  const problems = crossFileProblems(config);
  if (problems.length > 0) {
    throw new ConfigError(`Invalid configuration in ${dir}:\n${problems.join("\n")}`);
  }
  return config;
}
