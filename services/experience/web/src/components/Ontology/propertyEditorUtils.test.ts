import { describe, expect, it } from "vitest";
import {
  applyBulkPropertyPatch,
  automapStructFieldsFromKeys,
  buildEditableProperties,
  buildFormatsBySourceKey,
  collectStructSampleKeys,
  effectivePropertyVisibility,
  emptyStructFieldExport,
  isPropertyHidden,
  mapStructFieldColumnsByName,
  resolveDisplayTypeRule,
  serializePropertyEditor,
  sortPropertiesByVisibility,
  stripStructFieldColumns,
  suggestSharedApiName,
} from "./propertyEditorUtils";
import type { PropertyFormatRule, PropertyTypeRule, SharedPropertyType } from "../../api/knowledge";

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

  it("round-trips struct field description, main_field, and column", () => {
    const mapping = { address: "address_json" };
    const types: Record<string, PropertyTypeRule> = {
      address: {
        kind: "struct",
        properties: {
          city: {
            kind: "value_type",
            value_type: "String",
            description: "City name",
            main_field: true,
            column: "city_col",
          },
          zip: { kind: "value_type", value_type: "String" },
        },
      },
    };
    const editable = buildEditableProperties(mapping, types, {});
    const city = editable.find((p) => p.name === "address")?.structFields.find((f) => f.name === "city");
    expect(city?.description).toBe("City name");
    expect(city?.mainField).toBe(true);
    expect(city?.column).toBe("city_col");

    const serialized = serializePropertyEditor(editable);
    expect(serialized.property_types.address).toMatchObject({
      kind: "struct",
      properties: {
        city: {
          kind: "value_type",
          value_type: "String",
          description: "City name",
          main_field: true,
          column: "city_col",
        },
        zip: { kind: "value_type", value_type: "String" },
      },
    });
  });

  it("automaps struct fields from sample JSON keys", () => {
    const existing = [emptyStructFieldExport("city")];
    const next = automapStructFieldsFromKeys(["city", "zip", "country"], existing, "String");
    expect(next.map((f) => f.name)).toEqual(["city", "zip", "country"]);
    expect(collectStructSampleKeys([{ city: "Paris", zip: "75001" }, '{"country":"FR"}'])).toEqual([
      "city",
      "country",
      "zip",
    ]);
  });

  it("maps empty field columns by field name and strips columns for SPT", () => {
    const fields = [
      { ...emptyStructFieldExport("city"), column: "" },
      { ...emptyStructFieldExport("zip"), column: "zip_col" },
    ];
    expect(mapStructFieldColumnsByName(fields).map((f) => f.column)).toEqual(["city", "zip_col"]);
    expect(
      stripStructFieldColumns({
        city: { kind: "value_type", value_type: "String", column: "city_col" },
      }),
    ).toEqual({ city: { kind: "value_type", value_type: "String" } });
  });

  it("inherits SPT visibility and format when local property has none", () => {
    const types: Record<string, PropertyTypeRule> = {
      startDate: { kind: "shared_property_type", shared_property_type: "StartDate" },
    };
    const mapping = { startDate: "start_date" };
    const spts = [
      {
        tenant_id: "t",
        api_name: "StartDate",
        display_name: "Start date",
        value_type: "Date",
        description: "",
        visibility: "prominent" as const,
        property_format: { kind: "datetime" as const, style: "date" as const },
        created_at: "",
      },
    ];
    expect(effectivePropertyVisibility("start_date", types, mapping, spts)).toBe("prominent");
    expect(isPropertyHidden("start_date", types, mapping, spts)).toBe(false);
    const formats = buildFormatsBySourceKey({}, types, mapping, spts);
    expect(formats.get("start_date")).toEqual({ kind: "datetime", style: "date" });
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

  it("parses aliases preserving case", async () => {
    const { parseAliasesInput } = await import("./propertyEditorUtils");
    expect(parseAliasesInput("Hire Date, hire date\nOnboarding")).toEqual(["Hire Date", "Onboarding"]);
  });

  it("resolves effective property aliases from SPT", async () => {
    const { effectivePropertyAliases, propertyMatchesFilter } = await import("./propertyEditorUtils");
    const shared = [
      {
        tenant_id: "acme",
        api_name: "StartDate",
        display_name: "Start date",
        value_type: "Date",
        description: "",
        aliases: ["Hire Date"],
        created_at: "",
      },
    ];
    const types = { startDate: { kind: "shared_property_type" as const, shared_property_type: "StartDate" } };
    expect(effectivePropertyAliases("startDate", types, { startDate: "start_date" }, shared)).toEqual([
      "Start date",
      "StartDate",
      "Hire Date",
    ]);
    expect(propertyMatchesFilter("startDate", "hire", types, { startDate: "start_date" }, shared)).toBe(true);
    expect(propertyMatchesFilter("startDate", "zzz", types, { startDate: "start_date" }, shared)).toBe(false);
  });

  it("resolveDisplayTypeRule inherits render_hints/type_classes for an array of a value-typed SPT", () => {
    const shared: SharedPropertyType[] = [
      {
        tenant_id: "acme",
        api_name: "Sku",
        display_name: "SKU",
        value_type: "String",
        description: "",
        render_hints: ["identifier"],
        type_classes: ["priority"],
        created_at: "",
      },
    ];
    const typeRule: PropertyTypeRule = {
      kind: "array",
      element: { kind: "shared_property_type", shared_property_type: "Sku" },
    };
    const resolved = resolveDisplayTypeRule(typeRule, shared);
    // Same inheritance the scalar shared_property_type branch already gives —
    // an array of this SPT must render with the same hints as the SPT alone.
    expect(resolved?.render_hints).toEqual(["identifier"]);
    expect(resolved?.type_classes).toEqual(["priority"]);
  });
});
