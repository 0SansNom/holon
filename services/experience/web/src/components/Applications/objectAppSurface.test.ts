import { describe, expect, it } from "vitest";
import { declaredRelatedLinks, defaultApplicationDefinition } from "./objectAppSurface";

describe("declaredRelatedLinks", () => {
  it("exposes only explicitly declared links for the active object type", () => {
    const links = declaredRelatedLinks("Order", ["customer"], [{
      name: "sales.customer",
      urn: "hl:acme:main:relation-type:sales.customer",
      source_object_type_urn: "hl:acme:main:object-type:Order",
      target_object_type_urn: "hl:acme:main:object-type:Customer",
      source_api_name: "customer",
      source_display_name: "Customer",
      source_property: "customer_id",
      target_property: "orders",
      cardinality: "many-to-one",
    }]);

    expect(links).toEqual([{ linkName: "customer", label: "Customer → Customer", relatedType: "Customer" }]);
  });
});

describe("defaultApplicationDefinition", () => {
  it("returns an empty draft when the ontology has no visible types", () => {
    expect(defaultApplicationDefinition([{ name: "Secret", visibility: "hidden" }], [])).toEqual({
      surfaces: [],
      bindings: [],
      actionRefs: [],
    });
  });

  it("seeds the first visible type and a low-risk action", () => {
    const definition = defaultApplicationDefinition(
      [
        { name: "HiddenThing", visibility: "hidden" },
        { name: "Shipment", visibility: "normal" },
      ],
      [
        { name: "Shipment.release", target_object_type: "Shipment", risk_level: "low" },
        { name: "Shipment.destroy", target_object_type: "Shipment", risk_level: "high" },
      ],
    );
    expect(definition.surfaces).toEqual([{ type: "objectApp", objectType: "Shipment", route: "/apps/Shipment" }]);
    expect(definition.actionRefs).toEqual([{ action: "Shipment.release", riskClass: "low" }]);
    expect(definition.bindings?.map((binding) => binding.objectType)).toEqual(["Shipment", "Shipment"]);
  });
});
