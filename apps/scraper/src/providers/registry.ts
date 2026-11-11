import { providerStatuses, type Config, type ProviderName } from "@anazvara/shared";
import type { Db } from "@anazvara/database";
import { BudgetGuard, withBudget } from "./budget.js";
import { osmFactory } from "./osm.js";
import type { BudgetedProvider, Provider } from "./types.js";

export type Env = Readonly<Record<string, string | undefined>>;
export type ProviderFactory = (config: Config, env: Env) => Provider;
export type ProviderFactories = Partial<Record<ProviderName, ProviderFactory>>;

/** The one place providers are registered. */
export const PROVIDER_FACTORIES: ProviderFactories = { osm: osmFactory };

export interface BuiltProviders {
  providers: BudgetedProvider[];
  skipped: { name: ProviderName; reason: string }[];
}

/**
 * Builds every usable provider, each wrapped with the budget guard. `only`
 * limits the result to those names; the others are left out entirely.
 */
export function buildProviders(
  config: Config,
  env: Env,
  db: Db,
  factories: ProviderFactories = PROVIDER_FACTORIES,
  only?: readonly ProviderName[],
): BuiltProviders {
  const guard = new BudgetGuard(db, config.app.providers);
  const result: BuiltProviders = { providers: [], skipped: [] };

  for (const { name, active, reason } of providerStatuses(config.app, env)) {
    if (only && !only.includes(name)) continue;
    const factory = factories[name];
    if (!active) {
      result.skipped.push({ name, reason });
    } else if (factory === undefined) {
      result.skipped.push({ name, reason: "not implemented yet" });
    } else {
      const provider = factory(config, env);
      // Budget is counted under provider.name, so a mismatch would bill the wrong provider.
      if (provider.name !== name) {
        throw new Error(`Factory for ${name} built a provider named ${provider.name}`);
      }
      result.providers.push(withBudget(provider, guard));
    }
  }
  return result;
}
