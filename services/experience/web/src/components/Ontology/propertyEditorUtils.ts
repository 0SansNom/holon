import type {
  PropertyFormatRule,
  PropertyRenderHint,
  PropertyTypeRule,
  SharedPropertyType,
  ValueType,
} from "../../api/knowledge";

export type PropertyVisibility = "prominent" | "normal" | "hidden";

export const ALL_RENDER_HINTS: PropertyRenderHint[] = [
  "searchable",
  "sortable",
  "selectable",
  "identifier",
];

export type PropertyTypeKindOption =
  | "none"
  | "value_type"
  | "shared_property_type"
  | "struct"
  | "array";

export type StructFieldLeafKind = "value_type" | "shared_property_type";

export interface EditableStructField {
  name: string;
  leafKind: StructFieldLeafKind;
  valueType: string;
  sharedPropertyType: string;
}

export interface EditableProperty {
  name: string;
  column: string;
  typeKind: PropertyTypeKindOption;
  valueType: string;
  sharedPropertyType: string;
  /** Fields when typeKind === "struct". */
  structFields: EditableStructField[];
  /** Element typing when typeKind === "array". */
  arrayElementKind: "value_type" | "shared_property_type" | "struct";
  arrayElementValueType: string;
  arrayElementSharedPropertyType: string;
  arrayElementStructFields: EditableStructField[];
  editable: boolean;
  required: boolean;
  visibility: PropertyVisibility;
  /** Enabled render hints. Default includes searchable. */
  renderHints: PropertyRenderHint[];
  /** Type classes as comma-separated entry in UI; stored as string[]. */
  typeClasses: string[];
  formatKind: "" | PropertyFormatRule["kind"];
  formatCurrency: string;
  formatNumericStyle: "decimal" | "currency" | "percent" | "unit";
  formatUnit: string;
  formatNotation: "standard" | "compact" | "scientific" | "engineering";
  formatMaxFractionDigits: string;
  formatDatetimeStyle: Extract<PropertyFormatRule, { kind: "datetime" }>["style"];
  formatResourceType: "object-type" | "application";
  advancedFormat?: PropertyFormatRule;
}

function emptyStructField(seed = "field"): EditableStructField {
  return { name: seed, leafKind: "value_type", valueType: "", sharedPropertyType: "" };
}

function leafFromRule(rule: {
  kind?: string;
  value_type?: string;
  shared_property_type?: string;
}): EditableStructField {
  if (rule.kind === "shared_property_type") {
    return {
      name: "",
      leafKind: "shared_property_type",
      valueType: "",
      sharedPropertyType: rule.shared_property_type ?? "",
    };
  }
  return {
    name: "",
    leafKind: "value_type",
    valueType: rule.value_type ?? "",
    sharedPropertyType: "",
  };
}

function structFieldsFromRule(properties: Record<string, { kind?: string; value_type?: string; shared_property_type?: string }>): EditableStructField[] {
  return Object.entries(properties).map(([name, rule]) => ({ ...leafFromRule(rule), name }));
}

function leafToRule(field: EditableStructField): { kind: "value_type"; value_type: string } | { kind: "shared_property_type"; shared_property_type: string } | null {
  if (field.leafKind === "value_type" && field.valueType) {
    return { kind: "value_type", value_type: field.valueType };
  }
  if (field.leafKind === "shared_property_type" && field.sharedPropertyType) {
    return { kind: "shared_property_type", shared_property_type: field.sharedPropertyType };
  }
  return null;
}

export function buildEditableProperties(
  propertyMapping: Record<string, string>,
  propertyTypes: Record<string, PropertyTypeRule> = {},
  propertyFormats: Record<string, PropertyFormatRule> = {},
): EditableProperty[] {
  return Object.entries(propertyMapping).map(([name, column]) => {
    const typeRule = propertyTypes[name];
    const formatRule = propertyFormats[name];
    const base = emptyProperty(name);
    base.column = column;
    base.editable = typeRule?.editable ?? false;
    base.required = typeRule?.required ?? false;
    base.visibility = typeRule?.visibility ?? "normal";
    base.renderHints = typeRule?.render_hints ? [...typeRule.render_hints] : ["searchable"];
    base.typeClasses = typeRule?.type_classes ? [...typeRule.type_classes] : [];

    if (typeRule?.kind === "value_type") {
      base.typeKind = "value_type";
      base.valueType = typeRule.value_type;
    } else if (typeRule?.kind === "shared_property_type") {
      base.typeKind = "shared_property_type";
      base.sharedPropertyType = typeRule.shared_property_type;
    } else if (typeRule?.kind === "struct") {
      base.typeKind = "struct";
      base.structFields = structFieldsFromRule(typeRule.properties ?? {});
      if (base.structFields.length === 0) base.structFields = [emptyStructField("field1")];
    } else if (typeRule?.kind === "array") {
      base.typeKind = "array";
      const element = typeRule.element;
      if (element?.kind === "struct") {
        base.arrayElementKind = "struct";
        base.arrayElementStructFields = structFieldsFromRule(element.properties ?? {});
        if (base.arrayElementStructFields.length === 0) {
          base.arrayElementStructFields = [emptyStructField("field1")];
        }
      } else if (element?.kind === "shared_property_type") {
        base.arrayElementKind = "shared_property_type";
        base.arrayElementSharedPropertyType = element.shared_property_type;
      } else if (element?.kind === "value_type") {
        base.arrayElementKind = "value_type";
        base.arrayElementValueType = element.value_type;
      }
    }

    if (formatRule) {
      if (formatRule.kind === "currency") {
        base.formatKind = "currency";
        base.formatCurrency = formatRule.currency;
      } else if (formatRule.kind === "numeric") {
        base.formatKind = "numeric";
        base.formatNumericStyle = formatRule.style ?? "decimal";
        if (formatRule.currency) base.formatCurrency = formatRule.currency;
        if (formatRule.unit) base.formatUnit = formatRule.unit;
        if (formatRule.notation) base.formatNotation = formatRule.notation;
        if (formatRule.maximumFractionDigits != null) {
          base.formatMaxFractionDigits = String(formatRule.maximumFractionDigits);
        }
      } else if (formatRule.kind === "datetime") {
        base.formatKind = "datetime";
        base.formatDatetimeStyle = formatRule.style;
      } else if (formatRule.kind === "badge" || formatRule.kind === "principal") {
        base.formatKind = formatRule.kind;
        if (formatRule.kind === "badge") base.advancedFormat = formatRule;
      } else if (formatRule.kind === "resource-link") {
        base.formatKind = "resource-link";
        base.formatResourceType = formatRule.resourceType;
      } else {
        base.advancedFormat = formatRule;
      }
    }
    return base;
  });
}

function controlFlags(
  prop: EditableProperty,
): Pick<PropertyTypeRule, "editable" | "required" | "visibility" | "render_hints" | "type_classes"> {
  const flags: Pick<PropertyTypeRule, "editable" | "required" | "visibility" | "render_hints" | "type_classes"> =
    {};
  if (prop.editable) flags.editable = true;
  if (prop.required) flags.required = true;
  if (prop.visibility !== "normal") flags.visibility = prop.visibility;
  // Persist render_hints whenever they differ from the default ["searchable"].
  const hints = [...prop.renderHints].sort();
  const isDefaultSearchableOnly = hints.length === 1 && hints[0] === "searchable";
  if (!isDefaultSearchableOnly) {
    flags.render_hints = [...prop.renderHints];
  }
  if (prop.typeClasses.length > 0) {
    flags.type_classes = prop.typeClasses.filter(Boolean);
  }
  return flags;
}

function serializeStructFields(fields: EditableStructField[]): Record<string, { kind: "value_type"; value_type: string } | { kind: "shared_property_type"; shared_property_type: string }> | null {
  const properties: Record<string, { kind: "value_type"; value_type: string } | { kind: "shared_property_type"; shared_property_type: string }> = {};
  for (const field of fields) {
    const name = field.name.trim();
    if (!name) continue;
    const leaf = leafToRule(field);
    if (!leaf) continue;
    properties[name] = leaf;
  }
  return Object.keys(properties).length > 0 ? properties : null;
}

export function serializePropertyEditor(
  properties: EditableProperty[],
): {
  property_mapping: Record<string, string>;
  property_types: Record<string, PropertyTypeRule>;
  property_formats: Record<string, PropertyFormatRule>;
} {
  const property_mapping: Record<string, string> = {};
  const property_types: Record<string, PropertyTypeRule> = {};
  const property_formats: Record<string, PropertyFormatRule> = {};

  for (const prop of properties) {
    const name = prop.name.trim();
    const column = prop.column.trim();
    if (!name || !column) continue;
    property_mapping[name] = column;

    const flags = controlFlags(prop);

    if (prop.typeKind === "value_type" && prop.valueType) {
      property_types[name] = { ...flags, kind: "value_type", value_type: prop.valueType };
    } else if (prop.typeKind === "shared_property_type" && prop.sharedPropertyType) {
      property_types[name] = {
        ...flags,
        kind: "shared_property_type",
        shared_property_type: prop.sharedPropertyType,
      };
    } else if (prop.typeKind === "struct") {
      const propertiesMap = serializeStructFields(prop.structFields);
      if (propertiesMap) {
        property_types[name] = { ...flags, kind: "struct", properties: propertiesMap };
      }
    } else if (prop.typeKind === "array") {
      if (prop.arrayElementKind === "struct") {
        const propertiesMap = serializeStructFields(prop.arrayElementStructFields);
        if (propertiesMap) {
          property_types[name] = {
            ...flags,
            kind: "array",
            element: { kind: "struct", properties: propertiesMap },
          };
        }
      } else if (prop.arrayElementKind === "value_type" && prop.arrayElementValueType) {
        property_types[name] = {
          ...flags,
          kind: "array",
          element: { kind: "value_type", value_type: prop.arrayElementValueType },
        };
      } else if (prop.arrayElementKind === "shared_property_type" && prop.arrayElementSharedPropertyType) {
        property_types[name] = {
          ...flags,
          kind: "array",
          element: {
            kind: "shared_property_type",
            shared_property_type: prop.arrayElementSharedPropertyType,
          },
        };
      }
    } else if (Object.keys(flags).length > 0) {
      property_types[name] = flags;
    }

    if (prop.formatKind === "currency") {
      property_formats[name] = { kind: "currency", currency: prop.formatCurrency || "EUR" };
    } else if (prop.formatKind === "numeric") {
      const maxFrac = prop.formatMaxFractionDigits.trim();
      property_formats[name] = {
        kind: "numeric",
        style: prop.formatNumericStyle,
        ...(prop.formatNumericStyle === "currency" ? { currency: prop.formatCurrency || "EUR" } : {}),
        ...(prop.formatNumericStyle === "unit" && prop.formatUnit ? { unit: prop.formatUnit } : {}),
        ...(prop.formatNotation !== "standard" ? { notation: prop.formatNotation } : {}),
        ...(maxFrac !== "" && !Number.isNaN(Number(maxFrac))
          ? { maximumFractionDigits: Number(maxFrac) }
          : {}),
      };
    } else if (prop.formatKind === "datetime") {
      property_formats[name] = { kind: "datetime", style: prop.formatDatetimeStyle };
    } else if (prop.formatKind === "principal") {
      property_formats[name] = { kind: "principal" };
    } else if (prop.formatKind === "resource-link") {
      property_formats[name] = { kind: "resource-link", resourceType: prop.formatResourceType };
    } else if (prop.formatKind === "badge") {
      property_formats[name] =
        prop.advancedFormat?.kind === "badge" ? prop.advancedFormat : { kind: "badge", colors: {} };
    } else if (prop.advancedFormat) {
      property_formats[name] = prop.advancedFormat;
    }
  }

  return { property_mapping, property_types, property_formats };
}

export function propertyVisibilityOf(
  propertyApiName: string,
  propertyTypes?: Record<string, PropertyTypeRule> | null,
): PropertyVisibility {
  return propertyTypes?.[propertyApiName]?.visibility ?? "normal";
}

export function sortPropertiesByVisibility(
  keys: string[],
  propertyTypes?: Record<string, PropertyTypeRule> | null,
  mapping?: Record<string, string> | null,
): string[] {
  const score = (key: string) => {
    const vis = resolveVisibility(key, propertyTypes, mapping);
    if (vis === "hidden") return 2;
    if (vis === "prominent") return 0;
    return 1;
  };
  return [...keys].sort((a, b) => score(a) - score(b) || a.localeCompare(b));
}

function resolveVisibility(
  key: string,
  propertyTypes?: Record<string, PropertyTypeRule> | null,
  mapping?: Record<string, string> | null,
): PropertyVisibility {
  if (propertyVisibilityOf(key, propertyTypes) !== "normal") {
    return propertyVisibilityOf(key, propertyTypes);
  }
  if (!mapping) return "normal";
  for (const [apiName, column] of Object.entries(mapping)) {
    if (apiName === key || column === key) return propertyVisibilityOf(apiName, propertyTypes);
  }
  return "normal";
}

export function isPropertyHidden(
  key: string,
  propertyTypes?: Record<string, PropertyTypeRule> | null,
  mapping?: Record<string, string> | null,
): boolean {
  return resolveVisibility(key, propertyTypes, mapping) === "hidden";
}

export function emptyProperty(seedName = ""): EditableProperty {
  return {
    name: seedName,
    column: seedName,
    typeKind: "none",
    valueType: "",
    sharedPropertyType: "",
    structFields: [emptyStructField("field1")],
    arrayElementKind: "value_type",
    arrayElementValueType: "",
    arrayElementSharedPropertyType: "",
    arrayElementStructFields: [emptyStructField("field1")],
    editable: false,
    required: false,
    visibility: "normal",
    renderHints: ["searchable"],
    typeClasses: [],
    formatKind: "",
    formatCurrency: "EUR",
    formatNumericStyle: "decimal",
    formatUnit: "",
    formatNotation: "standard",
    formatMaxFractionDigits: "",
    formatDatetimeStyle: "datetime-short",
    formatResourceType: "object-type",
  };
}

export function emptyStructFieldExport(seed = "field"): EditableStructField {
  return emptyStructField(seed);
}

export function valueTypeOptions(valueTypes: ValueType[]): string[] {
  return valueTypes.map((v) => v.name).sort();
}

export function sharedPropertyOptions(shared: SharedPropertyType[]): string[] {
  return shared.map((s) => s.api_name).sort();
}

/** Suggest SPT api_name from a property API name (PascalCase-ish). */
export function suggestSharedApiName(propertyName: string): string {
  const cleaned = propertyName.replace(/[^a-zA-Z0-9_]/g, "");
  if (!cleaned) return "SharedProperty";
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

export function parseTypeClassesInput(raw: string): string[] {
  return raw
    .split(/[,\s]+/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export type BulkPropertyPatch = Partial<
  Pick<EditableProperty, "visibility" | "renderHints" | "typeClasses" | "formatKind" | "formatCurrency" | "formatNumericStyle" | "formatDatetimeStyle" | "formatResourceType">
>;

export function applyBulkPropertyPatch(
  properties: EditableProperty[],
  selectedNames: Set<string>,
  patch: BulkPropertyPatch,
): EditableProperty[] {
  if (selectedNames.size === 0 || Object.keys(patch).length === 0) return properties;
  return properties.map((p) => (selectedNames.has(p.name) ? { ...p, ...patch } : p));
}
