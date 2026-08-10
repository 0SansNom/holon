import { describe, expect, it } from "vitest";
import type { ActionDefinition } from "../../api/knowledge";
import { computeInlineEditableActions, urnShortName } from "./objectExplorerUtils";

describe("urnShortName", () => {
  it("returns the last colon segment", () => {
    expect(urnShortName("hl:acme:demo:object-type:Customer")).toBe("Customer");
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
