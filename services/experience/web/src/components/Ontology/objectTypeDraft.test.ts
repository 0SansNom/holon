import { describe, expect, it } from "vitest";
import type { ObjectType } from "../../api/knowledge";
import { emptyProperty } from "./propertyEditorUtils";
import {
  objectTypeDraftFormFromRecord,
  patchInterfacePropertyBinding,
  patchNestedBinding,
  prepareObjectTypePropose,
  relationTypesTouchingObjectType,
  toggleSetValue,
} from "./objectTypeDraft";

describe("prepareObjectTypePropose", () => {
  it("rejects invalid JSON and missing mappings", () => {
    const base = {
      conditionalFormatsJson: "{}",
      properties: [] as ReturnType<typeof emptyProperty>[],
      derivedProperties: [],
      primaryKey: "id",
      titleKey: "",
      description: "Customer",
      implements: [],
      linkConstraintBindings: {},
      interfacePropertyBindings: {},
      markings: [],
      projectUrn: "",
      pluralDisplayName: "Customers",
      lifecycleStatus: "experimental",
      deprecationReason: "",
      deprecationDeadline: "",
      replacementUrn: "",
      visibility: "normal",
      icon: "",
    };
    const invalidJson = prepareObjectTypePropose({ ...base, conditionalFormatsJson: "{" });
    expect(invalidJson.ok).toBe(false);
    if (!invalidJson.ok) expect(invalidJson.step).toBe("advanced");

    const missingMappings = prepareObjectTypePropose(base);
    expect(missingMappings.ok).toBe(false);
    if (!missingMappings.ok) expect(missingMappings.step).toBe("properties");

    const mapped = emptyProperty("id");
    mapped.column = "id";
    const withProps = { ...base, properties: [mapped] };
    const missingPrimaryKey = prepareObjectTypePropose({ ...withProps, primaryKey: "missing" });
    expect(missingPrimaryKey.ok).toBe(false);
    if (!missingPrimaryKey.ok) expect(missingPrimaryKey.step).toBe("identity");
    const ok = prepareObjectTypePropose(withProps);
    expect(ok.ok).toBe(true);
    if (ok.ok) {
      expect(ok.body.primary_key).toBe("id");
      expect(ok.body.property_mapping).toEqual({ id: "id" });
    }
  });
});

describe("draft helpers", () => {
  it("toggles set membership and nested bindings", () => {
    expect([...toggleSetValue(new Set(["a"]), "b")].sort()).toEqual(["a", "b"]);
    expect([...toggleSetValue(new Set(["a"]), "a")]).toEqual([]);
    expect(patchNestedBinding({}, "Reviewable", "owner", "ownedBy")).toEqual({
      Reviewable: { owner: "ownedBy" },
    });
    expect(patchInterfacePropertyBinding({ Reviewable: { city: "address.city" } }, "Reviewable", "city", "city")).toEqual(
      {},
    );
  });

  it("hydrates a draft form from a live ObjectType", () => {
    const form = objectTypeDraftFormFromRecord({
      name: "Customer",
      description: "A customer",
      property_mapping: { id: "id" },
      property_formats: {},
      implements: ["Reviewable"],
      primary_key: "id",
      title_key: "name",
      lifecycle_status: "active",
    } as unknown as ObjectType);
    expect(form.primaryKey).toBe("id");
    expect(form.titleKey).toBe("name");
    expect([...form.implements]).toEqual(["Reviewable"]);
    expect(form.properties.map((p) => p.name)).toEqual(["id"]);
  });

  it("filters relation types that touch the ObjectType", () => {
    const touching = relationTypesTouchingObjectType(
      [
        {
          source_object_type_urn: "hl:t:w:object-type:Order",
          target_object_type_urn: "hl:t:w:object-type:Customer",
        },
        {
          source_object_type_urn: "hl:t:w:object-type:Ticket",
          target_object_type_urn: "hl:t:w:object-type:Agent",
        },
      ],
      "Customer",
    );
    expect(touching).toHaveLength(1);
  });
});
