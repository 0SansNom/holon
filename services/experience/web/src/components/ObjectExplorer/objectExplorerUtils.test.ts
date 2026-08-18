import { describe, expect, it } from "vitest";
import type { ActionDefinition } from "../../api/knowledge";
import {
  computeInlineEditableActions,
  buildRelatedLinksForObjectType,
  buildExplorerColumnKeys,
  humanizeApiName,
  instancePropertyValue,
  parseInstanceUrn,
  parseSearchHitRef,
  preferTitleColumnFirst,
  preferredSearchProperty,
  resolveInstanceColumnKey,
  titleOf,
  urnShortName,
} from "./objectExplorerUtils";

describe("humanizeApiName", () => {
  it("spaces camelCase and snake_case", () => {
    expect(humanizeApiName("account_closed")).toBe("Account closed");
    expect(humanizeApiName("lifetimeValue")).toBe("Lifetime Value");
    expect(humanizeApiName("Customer.orders")).toBe("Orders");
  });
});

describe("urnShortName", () => {
  it("returns the last colon segment", () => {
    expect(urnShortName("hl:acme:main:object-type:Customer")).toBe("Customer");
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

describe("parseInstanceUrn", () => {
  it("parses ObjectType/id from the final segment", () => {
    expect(parseInstanceUrn("hl:acme:main:instance:Customer/9")).toEqual({ type: "Customer", id: "9" });
    expect(parseInstanceUrn("hl:acme:main:instance:Order/ord-1")).toEqual({ type: "Order", id: "ord-1" });
  });

  it("returns null when the local segment has no slash", () => {
    expect(parseInstanceUrn("hl:acme:main:object-type:Customer")).toBeNull();
  });
});

describe("buildRelatedLinksForObjectType", () => {
  it("hides hidden sides and sorts prominent first", () => {
    const links = buildRelatedLinksForObjectType("Order", [
      {
        name: "acme.orderCustomer",
        source_object_type_urn: "hl:t:w:object-type:Order",
        target_object_type_urn: "hl:t:w:object-type:Customer",
        source_api_name: "customer",
        source_display_name: "Customer",
        source_visibility: "normal",
        cardinality: "many_to_one",
      },
      {
        name: "acme.orderLines",
        source_object_type_urn: "hl:t:w:object-type:Order",
        target_object_type_urn: "hl:t:w:object-type:Line",
        source_api_name: "lines",
        source_display_name: "Lines",
        source_plural_display_name: "Order lines",
        source_visibility: "prominent",
        cardinality: "one_to_many",
      },
      {
        name: "acme.hidden",
        source_object_type_urn: "hl:t:w:object-type:Order",
        target_object_type_urn: "hl:t:w:object-type:Secret",
        source_api_name: "secret",
        source_visibility: "hidden",
      },
    ]);
    expect(links.map((l) => l.linkName)).toEqual(["lines", "customer"]);
    expect(links[0].visibility).toBe("prominent");
    expect(links[0].pluralLabel).toBe("Order lines");
  });

  it("skips pytest leftover RelationTypes", () => {
    const links = buildRelatedLinksForObjectType("Customer", [
      {
        name: "acme.orders",
        source_object_type_urn: "hl:t:w:object-type:Customer",
        target_object_type_urn: "hl:t:w:object-type:Order",
        source_api_name: "orders",
        source_display_name: "Orders",
      },
      {
        name: "Customer.ordersViaGenJoin_0118d88c",
        source_object_type_urn: "hl:t:w:object-type:Customer",
        target_object_type_urn: "hl:t:w:object-type:Order",
        source_api_name: "ordersViaGenJoin_0118d88c",
      },
    ]);
    expect(links.map((l) => l.linkName)).toEqual(["orders"]);
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

describe("buildExplorerColumnKeys", () => {
  const mapping = {
    id: "id",
    name: "name",
    email: "email",
    lifetimeValue: "lifetime_value",
  };

  it("orders ontology columns before action side-effect fields", () => {
    const row = {
      account_closed: false,
      credit_hold: true,
      email: "a@b.c",
      id: 1,
      lifetime_value: "10",
      name: "Acme",
      degraded: false,
    };
    expect(
      buildExplorerColumnKeys({ property_mapping: mapping, derived_properties: { mlValueTier: "fn" } }, row),
    ).toEqual(["id", "name", "email", "lifetime_value", "mlValueTier", "account_closed", "credit_hold"]);
  });

  it("falls back to row keys when mapping empty", () => {
    expect(buildExplorerColumnKeys(null, { id: 1, name: "x", degraded: false })).toEqual(["id", "name"]);
  });

  it("strips OSDK wire keys from fallback columns", () => {
    expect(
      buildExplorerColumnKeys(null, {
        __apiName: "Customer",
        __primaryKey: 1,
        __rid: "hl:acme:main:object:Customer/1",
        id: 1,
        name: "Acme",
      }),
    ).toEqual(["id", "name"]);
  });
});

describe("preferTitleColumnFirst", () => {
  it("moves title_key / name to the front", () => {
    expect(
      preferTitleColumnFirst(["id", "email", "name"], {
        title_key: "name",
        property_mapping: { id: "id", name: "name", email: "email" },
      }),
    ).toEqual(["name", "id", "email"]);
  });
});

describe("instancePropertyValue / resolveInstanceColumnKey", () => {
  it("reads snake_case backing columns from api names", () => {
    const row = { lifetime_value: "184500.00", name: "Acme" };
    const mapping = { lifetimeValue: "lifetime_value", name: "name" };
    expect(resolveInstanceColumnKey("lifetimeValue", mapping, new Set(Object.keys(row)))).toBe(
      "lifetime_value",
    );
    expect(instancePropertyValue(row, "lifetimeValue", mapping)).toBe("184500.00");
    expect(instancePropertyValue(row, "name", mapping)).toBe("Acme");
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
