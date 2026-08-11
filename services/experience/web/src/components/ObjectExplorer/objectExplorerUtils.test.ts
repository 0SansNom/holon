import { describe, expect, it } from "vitest";
import type { ActionDefinition } from "../../api/knowledge";
import {
  computeInlineEditableActions,
  parseSearchHitRef,
  preferredSearchProperty,
  titleOf,
  urnShortName,
} from "./objectExplorerUtils";

describe("urnShortName", () => {
  it("returns the last colon segment", () => {
    expect(urnShortName("hl:acme:demo:object-type:Customer")).toBe("Customer");
  });
});

describe("parseSearchHitRef", () => {
  it("parses objectType:tenant:id search docs", () => {
    expect(
      parseSearchHitRef({ urn: "Customer:acme:42", object_type: "Customer", tenant_id: "acme" }),
    ).toEqual({ type: "Customer", id: "42" });
  });

  it("keeps id segments that contain colons", () => {
    expect(
      parseSearchHitRef({ urn: "Order:acme:ord:99", object_type: "Order", tenant_id: "acme" }),
    ).toEqual({ type: "Order", id: "ord:99" });
  });

  it("returns null for malformed hits", () => {
    expect(parseSearchHitRef({ urn: "Customer:acme:42", object_type: "Order" })).toBeNull();
    expect(parseSearchHitRef({ urn: "Customer:acme", object_type: "Customer", tenant_id: "acme" })).toBeNull();
  });
});

describe("preferredSearchProperty", () => {
  it("prefers title_key when mapped", () => {
    expect(
      preferredSearchProperty({
        title_key: "name",
        primary_key: "id",
        property_mapping: { id: "id", name: "name" },
      }),
    ).toBe("name");
  });
});

describe("titleOf", () => {
  it("prefers title_key then primary_key", () => {
    expect(
      titleOf(
        { id: 7, name: "Acme" },
        { title_key: "name", primary_key: "id", property_mapping: { id: "id", name: "name" } },
      ),
    ).toBe("Acme");
    expect(
      titleOf({ id: 7, name: "Acme" }, { primary_key: "id", property_mapping: { id: "id", name: "name" } }),
    ).toBe("7");
  });

  it("resolves via property_mapping column", () => {
    expect(
      titleOf(
        { customer_name: "Globex" },
        { title_key: "name", primary_key: "id", property_mapping: { id: "id", name: "customer_name" } },
      ),
    ).toBe("Globex");
  });
});

describe("computeInlineEditableActions", () => {
  const action: ActionDefinition = {
    name: "Customer.setStatus",
    target_object_type: "Customer",
    required_permission: "write",
    risk_level: "low",
    description: "Set status",
    parameters: [{ name: "status", required: true, value_type: "string" }],
    edits: [{ property: "status", source: "parameter", parameter_name: "status" }],
  };

  it("maps a unique low-risk single-edit action to its property", () => {
    const map = computeInlineEditableActions("Customer", [], [action]);
    expect(map.get("status")?.name).toBe("Customer.setStatus");
  });

  it("ignores high-risk actions", () => {
    const map = computeInlineEditableActions("Customer", [], [{ ...action, risk_level: "high" }]);
    expect(map.size).toBe(0);
  });
});
