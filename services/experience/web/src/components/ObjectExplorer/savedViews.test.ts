import { describe, expect, it } from "vitest";
import {
  filterRowsByListIds,
  normalizeExploration,
  normalizeObjectList,
} from "./savedViews";

describe("normalizeExploration", () => {
  it("requires id, name, objectType", () => {
    expect(normalizeExploration({ name: "x", objectType: "Customer" })).toBeNull();
  });

  it("normalizes filters and layout", () => {
    const e = normalizeExploration({
      id: "1",
      name: "  Open  ",
      objectType: "Customer",
      objectSet: "vip",
      filters: [{ property: "status", op: "eq", value: "open" }],
      columnLayout: { order: ["status"], hidden: [], freezeCount: 1 },
    });
    expect(e?.name).toBe("Open");
    expect(e?.filters).toEqual([{ property: "status", op: "eq", value: "open" }]);
    expect(e?.columnLayout.freezeCount).toBe(1);
  });
});

describe("normalizeObjectList", () => {
  it("dedupes ids", () => {
    const list = normalizeObjectList({
      id: "l1",
      name: "Pick",
      objectType: "Customer",
      instanceIds: ["1", "2", "1", ""],
    });
    expect(list?.instanceIds).toEqual(["1", "2"]);
  });
});

describe("filterRowsByListIds", () => {
  it("keeps matching ids", () => {
    expect(
      filterRowsByListIds(
        [
          { id: 1, name: "a" },
          { id: "2", name: "b" },
          { id: 3, name: "c" },
        ],
        ["2", "3"],
      ),
    ).toEqual([
      { id: "2", name: "b" },
      { id: 3, name: "c" },
    ]);
  });
});
