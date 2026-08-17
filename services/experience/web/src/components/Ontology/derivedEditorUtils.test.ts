import { describe, expect, it } from "vitest";
import type { DerivedPropertyValue, RelationType } from "../../api/knowledge";
import {
  buildEditableDerived,
  linkNamesFromType,
  serializeDerivedProperties,
  typeAfterPath,
} from "./derivedEditorUtils";

const RELATIONS: RelationType[] = [
  {
    urn: "hl:acme:main:relation_type:Order.customer",
    name: "Order.customer",
    source_object_type_urn: "hl:acme:main:object_type:Order",
    target_object_type_urn: "hl:acme:main:object_type:Customer",
    source_property: "customerId",
    target_property: "orders",
    cardinality: "many_to_one",
  },
  {
    urn: "hl:acme:main:relation_type:ProductReview.order",
    name: "ProductReview.order",
    source_object_type_urn: "hl:acme:main:object_type:ProductReview",
    target_object_type_urn: "hl:acme:main:object_type:Order",
    source_property: "orderId",
    target_property: "reviews",
    cardinality: "many_to_one",
  },
];

describe("derivedEditorUtils", () => {
  it("lists reverse and forward link names", () => {
    expect(linkNamesFromType("Customer", RELATIONS)).toEqual(["orders"]);
    expect(linkNamesFromType("Order", RELATIONS)).toEqual(["customer", "reviews"]);
  });

  it("resolves multi-hop path types", () => {
    expect(typeAfterPath("Customer", ["orders", "reviews"], RELATIONS)).toBe("ProductReview");
  });

  it("round-trips link_aggregate path and collect", () => {
    const derived: Record<string, DerivedPropertyValue> = {
      reviewCount: {
        kind: "link_aggregate",
        path: ["orders", "reviews"],
        aggregate: "count",
      },
      productNames: {
        kind: "link_aggregate",
        path: ["orders"],
        aggregate: "collect_list",
        property: "product",
        collect_limit: 5,
      },
      tier: "lifetime_tier",
    };
    const editable = buildEditableDerived(derived);
    expect(editable.find((p) => p.name === "reviewCount")?.path).toEqual(["orders", "reviews"]);
    expect(editable.find((p) => p.name === "productNames")?.path).toEqual(["orders"]);
    expect(editable.find((p) => p.name === "tier")?.kind).toBe("function");

    const serialized = serializeDerivedProperties(editable);
    expect(serialized.reviewCount).toMatchObject({
      kind: "link_aggregate",
      path: ["orders", "reviews"],
      aggregate: "count",
    });
    expect(serialized.productNames).toMatchObject({
      kind: "link_aggregate",
      path: ["orders"],
      aggregate: "collect_list",
      property: "product",
      collect_limit: 5,
    });
    expect(serialized.tier).toBe("lifetime_tier");
  });
});
