import { describe, expect, test } from "vitest";
import { clean, instagramHandle } from "../../src/providers/normalize.js";

describe("instagramHandle", () => {
  test.each([
    ["theshop", "theshop"],
    ["@The.Shop_", "the.shop_"],
    ["https://www.instagram.com/theshop/", "theshop"],
    ["instagram.com/TheShop?igsh=abc", "theshop"],
    ["http://m.instagram.com/theshop#posts", "theshop"],
    ["  https://instagr.am/theshop  ", "theshop"],
  ])("%j → %s", (value, handle) => {
    expect(instagramHandle(value)).toBe(handle);
  });

  test.each([
    undefined,
    "",
    "   ",
    "https://facebook.com/theshop",
    "https://www.instagram.com/p/C123abc/",
    "https://www.instagram.com/reel/C123abc/",
    "the shop",
    "a".repeat(31),
  ])("%j is not a handle", (value) => {
    expect(instagramHandle(value)).toBeUndefined();
  });
});

describe("clean", () => {
  test("trims, and empty becomes undefined", () => {
    expect(clean("  Rang ")).toBe("Rang");
    expect(clean("   ")).toBeUndefined();
    expect(clean(undefined)).toBeUndefined();
  });
});
