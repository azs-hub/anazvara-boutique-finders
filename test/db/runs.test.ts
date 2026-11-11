import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { openDb, type Db } from "../../src/db/client.js";
import { finishRun, getRun, startRun } from "../../src/db/runs.js";

let db: Db;

beforeEach(() => {
  db = openDb(":memory:");
});

afterEach(() => {
  db.$client.close();
});

describe("startRun", () => {
  test("stores the city, params and start time and leaves the run open", () => {
    const run = startRun(db, {
      city: "Goa",
      params: { target: 50 },
      now: new Date("2026-11-05T10:00:00.000Z"),
    });

    expect(run).toMatchObject({
      city: "Goa",
      params: { target: 50 },
      startedAt: "2026-11-05T10:00:00.000Z",
      finishedAt: null,
      statsJson: null,
    });
  });

  test("defaults params to an empty object", () => {
    expect(startRun(db, { city: "Goa" }).params).toEqual({});
  });
});

describe("finishRun", () => {
  test("records the finish time and stats", () => {
    const { id } = startRun(db, { city: "Goa" });

    const finished = finishRun(db, id, { leads: 3 }, new Date("2026-11-05T11:00:00.000Z"));

    expect(finished).toMatchObject({
      id,
      finishedAt: "2026-11-05T11:00:00.000Z",
      statsJson: { leads: 3 },
    });
  });

  test("throws when the run does not exist", () => {
    expect(() => finishRun(db, 999, {})).toThrow("Run 999 not found");
  });
});

describe("getRun", () => {
  test("returns a stored run", () => {
    const run = startRun(db, { city: "Goa" });

    expect(getRun(db, run.id)).toEqual(run);
  });

  test("returns undefined for an unknown id", () => {
    expect(getRun(db, 999)).toBeUndefined();
  });
});
