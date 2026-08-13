import { describe, expect, it } from "vitest";
import {
  buildPredicateDefinition,
  expandFilterPropertyKeys,
  matchesPredicates,
  parsePredicateValue,
  predicateValueToInput,
} from "../Ontology/objectSetPredicates";

describe("parsePredicateValue", () => {
  it("parses numbers and in-lists", () => {
    expect(parsePredicateValue("eq", "42")).toBe(42);
    expect(parsePredicateValue("eq", "shipped")).toBe("shipped");
    expect(parsePredicateValue("in", "a, 2, b")).toEqual(["a", 2, "b"]);
  });
});

describe("buildPredicateDefinition", () => {
  it("drops incomplete rows", () => {
    expect(
      buildPredicateDefinition([
        { property: "status", op: "eq", value: "shipped" },
        { property: "name", op: "contains", value: "" },
        { property: "", op: "eq", value: "x" },
      ]),
    ).toEqual({ all: [{ property: "status", op: "eq", value: "shipped" }] });
  });
});

describe("matchesPredicates", () => {
  const mapping = { status: "status", name: "name", amount: "amount" };

  it("matches eq and contains", () => {
    expect(
      matchesPredicates({ status: "active" }, { all: [{ property: "status", op: "eq", value: "active" }] }, mapping),
    ).toBe(true);
    expect(
      matchesPredicates({ status: "churned" }, { all: [{ property: "status", op: "eq", value: "active" }] }, mapping),
    ).toBe(false);
    expect(
      matchesPredicates(
        { name: "Acme Corp" },
        { all: [{ property: "name", op: "contains", value: "Acme" }] },
        mapping,
      ),
    ).toBe(true);
  });

  it("matches in and comparisons", () => {
    expect(
      matchesPredicates(
        { status: "gold" },
        { all: [{ property: "status", op: "in", value: ["gold", "silver"] }] },
        mapping,
      ),
    ).toBe(true);
    expect(
      matchesPredicates({ amount: 100 }, { all: [{ property: "amount", op: "gte", value: 100 }] }, mapping),
    ).toBe(true);
    expect(
      matchesPredicates({ amount: 99 }, { all: [{ property: "amount", op: "gte", value: 100 }] }, mapping),
    ).toBe(false);
  });

  it("resolves via property_mapping column", () => {
    expect(
      matchesPredicates(
        { st: "open" },
        { all: [{ property: "status", op: "eq", value: "open" }] },
        { status: "st" },
      ),
    ).toBe(true);
  });

  it("matches one-level struct field paths", () => {
    const mapping = { address: "address_json", id: "id" };
    expect(
      matchesPredicates(
        { address: { city: "Paris", zip: "75001" } },
        { all: [{ property: "address.city", op: "eq", value: "Paris" }] },
        mapping,
      ),
    ).toBe(true);
    expect(
      matchesPredicates(
        { address_json: { city: "Lyon" } },
        { all: [{ property: "address.city", op: "contains", value: "yo" }] },
        mapping,
      ),
    ).toBe(true);
    expect(
      matchesPredicates(
        { address: { city: "Paris" } },
        { all: [{ property: "address.city", op: "eq", value: "Lyon" }] },
        mapping,
      ),
    ).toBe(false);
  });
});

describe("expandFilterPropertyKeys", () => {
  it("adds struct field paths", () => {
    expect(
      expandFilterPropertyKeys(
        { id: "id", address: "address_json" },
        {
          address: {
            kind: "struct",
            properties: {
              city: { kind: "value_type", value_type: "String" },
              zip: { kind: "value_type", value_type: "String" },
            },
          },
        },
      ),
    ).toEqual(["id", "address", "address.city", "address.zip"]);
  });
});

describe("predicateValueToInput", () => {
  it("round-trips in lists", () => {
    expect(predicateValueToInput("in", ["a", 2])).toBe("a, 2");
  });
});
