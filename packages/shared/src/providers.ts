import { PROVIDER_NAMES, type AppConfig, type ProviderName } from "./schema.js";

export interface ProviderStatus {
  name: ProviderName;
  active: boolean;
  /** Why the provider is active or not. Never contains the value of a key. */
  reason: string;
}

/** A provider runs only if it is enabled in config and its required env var is set. */
export function providerStatuses(
  app: AppConfig,
  env: Readonly<Record<string, string | undefined>> = process.env,
): ProviderStatus[] {
  return PROVIDER_NAMES.map((name) => {
    const { enabled, requiredEnv } = app.providers[name];
    if (!enabled) return { name, active: false, reason: "disabled in config/app.json" };
    // .env.example leaves keys empty, so an empty value counts as not set.
    if (requiredEnv !== null && !env[requiredEnv]?.trim()) {
      return { name, active: false, reason: `${requiredEnv} is not set` };
    }
    return { name, active: true, reason: "active" };
  });
}
