import { describe, expect, it } from "vitest";
import type { InterfaceType } from "../../api/knowledge/types";
import { effectiveInterfaceContract } from "./interfaceContractUtils";

function iface(partial: Partial<InterfaceType> & { name: string }): InterfaceType {
  return {
    tenant_id: "test",
    required_properties: [],
    required_actions: [],
    description: "",
    created_at: "2026-01-01T00:00:00Z",
    ...partial,
  };
}

describe("effectiveInterfaceContract", () => {
  it("merges parent required properties and marks inheritance order", () => {
    const parent = iface({ name: "Parent", required_properties: ["email"] });
    const child = iface({
      name: "Child",
      parent_interfaces: ["Parent"],
      required_properties: ["phone"],
    });
    const byName = new Map([
      ["Parent", parent],
      ["Child", child],
    ]);
    const effective = effectiveInterfaceContract(child, byName);
    expect(effective.required_properties).toEqual(["email", "phone"]);
  });

  it("inherits link constraints under child identity", () => {
    const parent = iface({
      name: "Parent",
      link_constraints: [
        {
          api_name: "customer",
          target_kind: "object_type",
          target: "Customer",
          cardinality: "one",
          required: true,
        },
      ],
    });
    const child = iface({ name: "Child", parent_interfaces: ["Parent"] });
    const effective = effectiveInterfaceContract(
      child,
      new Map([
        ["Parent", parent],
        ["Child", child],
      ]),
    );
    expect(effective.link_constraints).toHaveLength(1);
    expect(effective.link_constraints[0].api_name).toBe("customer");
  });
});
