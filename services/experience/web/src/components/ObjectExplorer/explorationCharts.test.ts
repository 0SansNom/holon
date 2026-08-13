import { describe, expect, it } from "vitest";
import {
  aggregateCategorical,
  aggregateHistogram,
  bucketToFilterPredicates,
  buildExplorationSeries,
  isMostlyNumeric,
  mergeDrillDownFilters,
} from "./explorationCharts";

describe("isMostlyNumeric", () => {
  it("detects numeric-dominant series", () => {
    expect(isMostlyNumeric([1, 2, "3", null])).toBe(true);
    expect(isMostlyNumeric(["a", "b", 1])).toBe(false);
  });
});

describe("aggregateCategorical", () => {
  it("counts and caps with Other", () => {
    const rows = Array.from({ length: 15 }, (_, i) => ({ status: `s${i}` }));
    const buckets = aggregateCategorical(rows, "status", {}, 10);
    expect(buckets).toHaveLength(11);
    expect(buckets.at(-1)?.label.startsWith("Other")).toBe(true);
  });
});

describe("aggregateHistogram", () => {
  it("bins numeric values", () => {
    const rows = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => ({ amount: n }));
    const buckets = aggregateHistogram(rows, "amount", {}, 5);
    expect(buckets.length).toBe(5);
    expect(buckets.reduce((s, b) => s + b.count, 0)).toBe(10);
  });
});

describe("buildExplorationSeries", () => {
  it("picks histogram for numeric auto", () => {
    const series = buildExplorationSeries([{ n: 1 }, { n: 2 }, { n: 3 }], "n", "auto");
    expect(series?.kind).toBe("histogram");
    expect(series?.mode).toBe("numeric");
  });

  it("picks bar for categorical auto", () => {
    const series = buildExplorationSeries([{ s: "a" }, { s: "b" }], "s", "auto");
    expect(series?.kind).toBe("bar");
  });
});

describe("drill-down filters", () => {
  it("eq for categorical buckets", () => {
    expect(bucketToFilterPredicates("status", { label: "open", count: 2, value: "open" })).toEqual([
      { property: "status", op: "eq", value: "open" },
    ]);
  });

  it("range for histogram bins", () => {
    expect(
      bucketToFilterPredicates("amount", { label: "0–10", count: 3, min: 0, max: 10, lastBin: false }),
    ).toEqual([
      { property: "amount", op: "gte", value: "0" },
      { property: "amount", op: "lt", value: "10" },
    ]);
  });

  it("replaces prior predicates for the same property", () => {
    const merged = mergeDrillDownFilters(
      [
        { property: "status", op: "eq", value: "old" },
        { property: "name", op: "contains", value: "Acme" },
      ],
      "status",
      { label: "new", count: 1, value: "new" },
    );
    expect(merged).toEqual([
      { property: "name", op: "contains", value: "Acme" },
      { property: "status", op: "eq", value: "new" },
    ]);
  });
});
