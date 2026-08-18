import { describe, expect, it } from "vitest";
import { declaredRelatedLinks } from "./objectAppSurface";

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
