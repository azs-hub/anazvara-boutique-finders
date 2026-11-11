import { existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { CONFIG_DIR, REPO_ROOT, resolveFromRoot } from "../src/paths.js";

describe("paths", () => {
  it("finds the repo root and its config directory", () => {
    expect(existsSync(join(REPO_ROOT, "pnpm-workspace.yaml"))).toBe(true);
    expect(CONFIG_DIR).toBe(join(REPO_ROOT, "config"));
  });

  it("resolves relative paths against the repo root", () => {
    expect(resolveFromRoot("data/stockists.db")).toBe(join(REPO_ROOT, "data", "stockists.db"));
  });

  it("leaves absolute paths alone", () => {
    expect(resolveFromRoot("/tmp/x.db")).toBe("/tmp/x.db");
  });
});
