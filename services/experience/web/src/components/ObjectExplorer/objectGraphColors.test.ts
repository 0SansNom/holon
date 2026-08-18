import { describe, expect, it } from "vitest";
import { colorForObjectType } from "./objectGraphColors";

describe("colorForObjectType", () => {
  it("is stable for a given type and defined for unknown types", () => {
    expect(colorForObjectType("Shipment")).toBe(colorForObjectType("Shipment"));
    expect(colorForObjectType("Shipment")).toMatch(/^#[0-9a-f]{6}$/);
    expect(colorForObjectType("Customer")).not.toBe(colorForObjectType("Order"));
  });
});
