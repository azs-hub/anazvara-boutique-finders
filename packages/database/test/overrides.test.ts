import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { openDb, type Db } from "../src/client.js";
import { applyOverrides, getOverrides, setOverride } from "../src/overrides.js";
import { insertBusiness } from "./helpers.js";

let db: Db;

beforeEach(() => {
  db = openDb(":memory:");
});

afterEach(() => {
  db.$client.close();
});

describe("setOverride", () => {
  test("stores the value, note and edit time", () => {
    const business = insertBusiness(db);

    const row = setOverride(db, business.id, "physical_store", "yes", "Visited in person", new Date(NOW_A));

    expect(row).toEqual({
      businessId: business.id,
      field: "physical_store",
      value: "yes",
      note: "Visited in person",
      editedAt: NOW_A,
    });
  });

  test("a second call for the same field replaces the value, note and edit time", () => {
    const business = insertBusiness(db);
    setOverride(db, business.id, "physical_store", "yes", "first", new Date(NOW_A));

    const row = setOverride(db, business.id, "physical_store", "no", undefined, new Date(NOW_B));

    expect(row).toMatchObject({ value: "no", note: null, editedAt: NOW_B });
    expect(getOverrides(db, business.id)).toEqual({ physical_store: "no" });
  });

  test.each(["id", "first_seen_run", "updated_at", "not_a_column", "physicalStore"])(
    "refuses to lock %s",
    (field) => {
      const business = insertBusiness(db);

      expect(() => setOverride(db, business.id, field, "x")).toThrow(`Cannot override businesses.${field}`);
    },
  );

  test("rejects a business that does not exist", () => {
    expect(() => setOverride(db, 999, "name", "x")).toThrow(/FOREIGN KEY/);
  });
});

describe("getOverrides", () => {
  test("maps each field to its JSON value, for one business only", () => {
    const business = insertBusiness(db);
    const other = insertBusiness(db);
    setOverride(db, business.id, "do_not_contact", true);
    setOverride(db, business.id, "score", 72.5);
    setOverride(db, business.id, "contact_person", null);
    setOverride(db, other.id, "name", "Other");

    expect(getOverrides(db, business.id)).toEqual({ do_not_contact: true, score: 72.5, contact_person: null });
  });

  test("is empty when nothing is locked", () => {
    expect(getOverrides(db, insertBusiness(db).id)).toEqual({});
  });
});

describe("applyOverrides", () => {
  test("puts the user's values on top and leaves the rest and the input unchanged", () => {
    const business = insertBusiness(db, { name: "Scraped Name", physicalStore: "unknown", city: "Panaji" });

    const merged = applyOverrides(business, { name: "Real Name", physical_store: "yes", contact_person: null });

    expect(merged).toEqual({ ...business, name: "Real Name", physicalStore: "yes", contactPerson: null });
    expect(business.name).toBe("Scraped Name");
  });

  test("ignores fields that are not overridable businesses columns", () => {
    const business = insertBusiness(db);

    expect(applyOverrides(business, { id: 999, dropped_column: "x", physicalStore: "yes" })).toEqual(business);
  });
});

const NOW_A = "2026-11-05T10:00:00.000Z";
const NOW_B = "2026-11-06T10:00:00.000Z";
