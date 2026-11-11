import { resolve } from "node:path";
import { defineConfig } from "drizzle-kit";

// drizzle-kit runs from packages/database (the root db:* scripts use --filter) and loads this file as
// CommonJS, so import.meta is unavailable; like schema and out, the root is found from the working
// directory. DATABASE_PATH, as everywhere else, is relative to the repo root.
const REPO_ROOT = resolve("../..");

export default defineConfig({
  dialect: "sqlite",
  schema: "./src/schema.ts",
  out: "./drizzle",
  dbCredentials: {
    url: resolve(REPO_ROOT, process.env.DATABASE_PATH ?? "data/stockists.db"),
  },
});
