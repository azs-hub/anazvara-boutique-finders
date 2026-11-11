import { isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** The repository root, found from this file so it doesn't depend on the working directory. */
export const REPO_ROOT = fileURLToPath(new URL("../../..", import.meta.url));

/** Resolves a relative path against the repo root; absolute paths are returned unchanged. */
export function resolveFromRoot(path: string): string {
  return isAbsolute(path) ? path : resolve(REPO_ROOT, path);
}

/** config/ at the repo root. */
export const CONFIG_DIR = join(REPO_ROOT, "config");
