import { describe, expect, test } from "vitest";
import { formatTable } from "../../src/cli/table.js";

describe("formatTable", () => {
  test("pads columns to the widest cell and underlines the header", () => {
    expect(formatTable(["key", "n"], [["goa", "25"], ["mumbai", "3"]])).toEqual([
      "key     n",
      "------  --",
      "goa     25",
      "mumbai  3",
    ]);
  });

  test("prints only the header when there are no rows", () => {
    expect(formatTable(["key"], [])).toEqual(["key", "---"]);
  });
});
