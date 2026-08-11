import { describe, expect, it } from "vitest";
import {
  applyBulkPropertyPatch,
  buildEditableProperties,
  isPropertyHidden,
  serializePropertyEditor,
  sortPropertiesByVisibility,
  suggestSharedApiName,
} from "./propertyEditorUtils";
import type { PropertyFormatRule, PropertyTypeRule } from "../../api/knowledge";

describe("propertyEditorUtils", () => {
  it("round-trips mapping, value type, format, and visibility", () => {
    const mapping = { id: "id", lifetimeValue: "lifetime_value", segment: "segment" };
    const types: Record<string, PropertyTypeRule> = {
      lifetimeValue: { kind: "value_type", value_type: "Money", visibility: "prominent" },
      segment: { visibility: "hidden", editable: true },
    };
    const formats: Record<string, PropertyFormatRule> = {
      lifetimeValue: { kind: "currency", currency: "EUR" },
    };
    const editable = buildEditableProperties(mapping, types, formats);
    expect(editable).toHaveLength(3);
    expect(editable.find((p) => p.name === "lifetimeValue")?.formatKind).toBe("currency");
    expect(editable.find((p) => p.name === "segment")?.visibility).toBe("hidden");

    const serialized = serializePropertyEditor(editable);
    expect(serialized.property_mapping).toEqual(mapping);
    expect(serialized.property_types.lifetimeValue).toMatchObject({
      kind: "value_type",
      value_type: "Money",
      visibility: "prominent",
    });
    expect(serialized.property_types.segment).toMatchObject({ visibility: "hidden", editable: true });
    expect(serialized.property_formats.lifetimeValue).toEqual({ kind: "currency", currency: "EUR" });
  });

  it("round-trips struct and array-of-struct types", () => {
    const mapping = { address: "address_json", tags: "tags_json" };
    const types: Record<string, PropertyTypeRule> = {
      address: {
        kind: "struct",
        properties: { city: { kind: "value_type", value_type: "String" } },
      },
      tags: {
        kind: "array",
        element: {
          kind: "struct",
          properties: { label: { kind: "value_type", value_type: "String" } },
        },
      },
    };
    const editable = buildEditableProperties(mapping, types, {});
    expect(editable.find((p) => p.name === "address")?.typeKind).toBe("struct");
    expect(editable.find((p) => p.name === "address")?.structFields[0]?.name).toBe("city");
    expect(editable.find((p) => p.name === "tags")?.arrayElementKind).toBe("struct");

    const serialized = serializePropertyEditor(editable);
    expect(serialized.property_types.address).toMatchObject({
      kind: "struct",
      properties: { city: { kind: "value_type", value_type: "String" } },
    });
    expect(serialized.property_types.tags).toMatchObject({
      kind: "array",
      element: { kind: "struct", properties: { label: { kind: "value_type", value_type: "String" } } },
    });
  });

  it("sorts prominent first and hides hidden keys", () => {
    const types: Record<string, PropertyTypeRule> = {
      name: { visibility: "prominent" },
      secret: { visibility: "hidden" },
    };
    const mapping = { name: "name", secret: "secret", id: "id" };
    const sorted = sortPropertiesByVisibility(["id", "secret", "name"], types, mapping);
    expect(sorted[0]).toBe("name");
    expect(isPropertyHidden("secret", types, mapping)).toBe(true);
    expect(isPropertyHidden("name", types, mapping)).toBe(false);
  });

  it("round-trips render_hints and type_classes; omits default searchable-only", () => {
    const mapping = { id: "id", name: "name", secret: "secret" };
    const types: Record<string, PropertyTypeRule> = {
      name: {
        visibility: "prominent",
        render_hints: ["searchable", "sortable"],
        type_classes: ["important"],
      },
      secret: { render_hints: [], type_classes: ["internal"] },
    };
    const editable = buildEditableProperties(mapping, types, {});
    expect(editable.find((p) => p.name === "name")?.renderHints).toEqual(["searchable", "sortable"]);
    expect(editable.find((p) => p.name === "secret")?.renderHints).toEqual([]);
    expect(editable.find((p) => p.name === "id")?.renderHints).toEqual(["searchable"]);

    const serialized = serializePropertyEditor(editable);
    expect(serialized.property_types.name).toMatchObject({
      visibility: "prominent",
      render_hints: ["searchable", "sortable"],
      type_classes: ["important"],
    });
    expect(serialized.property_types.secret).toMatchObject({
      render_hints: [],
      type_classes: ["internal"],
    });
    // Default searchable-only is not persisted when nothing else is set.
    expect(serialized.property_types.id).toBeUndefined();
  });

  it("applies bulk visibility/format patches", () => {
    const editable = buildEditableProperties(
      { a: "a", b: "b", c: "c" },
      {},
      {},
    );
    const next = applyBulkPropertyPatch(editable, new Set(["a", "c"]), {
      visibility: "hidden",
      formatKind: "currency",
    });
    expect(next.find((p) => p.name === "a")?.visibility).toBe("hidden");
    expect(next.find((p) => p.name === "a")?.formatKind).toBe("currency");
    expect(next.find((p) => p.name === "b")?.visibility).toBe("normal");
    expect(next.find((p) => p.name === "c")?.formatKind).toBe("currency");
  });

  it("suggests shared api names", () => {
    expect(suggestSharedApiName("startDate")).toBe("StartDate");
  });
});
