import { describe, expect, it } from "vitest";
import { comparePropertyKeys, csvEscape, rowsToCsv, valuesDiffer } from "./csvExport";

describe("csvEscape", () => {
  it("quotes fields with commas and quotes", () => {
    expect(csvEscape("a,b")).toBe('"a,b"');
    expect(csvEscape('say "hi"')).toBe('"say ""hi"""');
    expect(csvEscape(null)).toBe("");
  });
});

describe("rowsToCsv", () => {
  it("builds header + rows", () => {
    expect(rowsToCsv([{ id: 1, name: "Acme", materializedAt: "x" }], ["id", "name"])).toBe(
      "id,name\n1,Acme",
    );
  });
});

describe("compare helpers", () => {
  it("unions keys and detects diffs", () => {
    expect(comparePropertyKeys([{ id: 1, a: 1 }, { id: 2, b: 2 }])).toEqual(["a", "b", "id"]);
    expect(valuesDiffer(1, 2)).toBe(true);
    expect(valuesDiffer({ x: 1 }, { x: 1 })).toBe(false);
  });
});
