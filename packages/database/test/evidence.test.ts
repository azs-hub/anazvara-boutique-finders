import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { openDb, type Db } from "../src/client.js";
import { addEvidence, listEvidence } from "../src/evidence.js";
import { insertBusiness } from "./helpers.js";

let db: Db;

beforeEach(() => {
  db = openDb(":memory:");
});

afterEach(() => {
  db.$client.close();
});

describe("addEvidence", () => {
  test("stores the row with a creation time and null optional links", () => {
    const business = insertBusiness(db);

    const row = addEvidence(db, {
      businessId: business.id,
      field: "physical_store",
      value: "yes",
      source: "website",
      sourceUrl: "https://shop.example/contact",
      quote: "Visit our store in Assagao",
      confidence: "high",
      now: new Date("2026-11-05T10:00:00.000Z"),
    });

    expect(row).toMatchObject({
      businessId: business.id,
      field: "physical_store",
      value: "yes",
      source: "website",
      quote: "Visit our store in Assagao",
      confidence: "high",
      sightingId: null,
      pageId: null,
      runId: null,
      createdAt: "2026-11-05T10:00:00.000Z",
    });
  });

  test("rejects a business that does not exist", () => {
    expect(() => addEvidence(db, { businessId: 999, field: "address", value: "x", source: "user" })).toThrow(
      /FOREIGN KEY/,
    );
  });
});

describe("listEvidence", () => {
  test("lists a business's evidence oldest first, optionally for one field", () => {
    const business = insertBusiness(db);
    const other = insertBusiness(db);
    const add = (businessId: number, field: string, value: string) =>
      addEvidence(db, { businessId, field, value, source: "website" });

    const first = add(business.id, "physical_store", "yes");
    const address = add(business.id, "address", "Assagao");
    const second = add(business.id, "physical_store", "no");
    add(other.id, "physical_store", "yes");

    expect(listEvidence(db, business.id).map((row) => row.id)).toEqual([first.id, address.id, second.id]);
    expect(listEvidence(db, business.id, "physical_store").map((row) => row.id)).toEqual([first.id, second.id]);
    expect(listEvidence(db, business.id, "multi_brand")).toEqual([]);
  });
});
