import { describe, expect, it } from "vitest";
import {
  normalizeColumnLayout,
  resolveVisibleColumnOrder,
  toggleHidden,
} from "./columnLayout";

describe("resolveVisibleColumnOrder", () => {
  it("uses default availability order when layout empty", () => {
    expect(resolveVisibleColumnOrder(["b", "a", "c"], null).visibleOrder).toEqual(["b", "a", "c"]);
  });

  it("applies saved order and appends new keys", () => {
    const layout = normalizeColumnLayout({ order: ["c", "a"], hidden: [], freezeCount: 1 });
    expect(resolveVisibleColumnOrder(["a", "b", "c"], layout)).toEqual({
      visibleOrder: ["c", "a", "b"],
      freezeCount: 1,
      allOrdered: ["c", "a", "b"],
    });
  });

  it("hides columns but keeps at least one", () => {
    const layout = normalizeColumnLayout({ order: ["a", "b"], hidden: ["a", "b"], freezeCount: 2 });
    expect(resolveVisibleColumnOrder(["a", "b"], layout).visibleOrder).toEqual(["a"]);
    expect(resolveVisibleColumnOrder(["a", "b"], layout).freezeCount).toBe(1);
  });
});

describe("toggleHidden", () => {
  it("adds and removes hidden keys", () => {
    const base = normalizeColumnLayout({ order: ["a"], hidden: [], freezeCount: 0 });
    const hidden = toggleHidden(base, "a", true);
    expect(hidden.hidden).toEqual(["a"]);
    expect(toggleHidden(hidden, "a", false).hidden).toEqual([]);
  });
});
