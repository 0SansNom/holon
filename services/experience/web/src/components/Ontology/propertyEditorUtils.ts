import type {
  PropertyFormatRule,
  PropertyLifecycleStatus,
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
  "keywords",
  "long_text",
  "low_cardinality",
  "enable_leading_wildcards",
  "enable_regex_queries",
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
  description: string;
  mainField: boolean;
  /** Optional per-field dataset column (Foundry Column mapping). */
  column: string;
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
  /** Foundry property status. */
  lifecycleStatus: PropertyLifecycleStatus;
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
  return {
    name: seed,
    leafKind: "value_type",
    valueType: "",
    sharedPropertyType: "",
    description: "",
    mainField: false,
    column: "",
  };
}

function leafFromRule(rule: {
  kind?: string;
  value_type?: string;
  shared_property_type?: string;
  description?: string;
  main_field?: boolean;
  column?: string;
}): EditableStructField {
  if (rule.kind === "shared_property_type") {
    return {
      name: "",
      leafKind: "shared_property_type",
      valueType: "",
      sharedPropertyType: rule.shared_property_type ?? "",
      description: rule.description ?? "",
      mainField: !!rule.main_field,
      column: rule.column ?? "",
    };
  }
  return {
    name: "",
    leafKind: "value_type",
    valueType: rule.value_type ?? "",
    sharedPropertyType: "",
    description: rule.description ?? "",
    mainField: !!rule.main_field,
    column: rule.column ?? "",
  };
}

function structFieldsFromRule(
  properties: Record<
    string,
    {
      kind?: string;
      value_type?: string;
      shared_property_type?: string;
      description?: string;
      main_field?: boolean;
      column?: string;
    }
  >,
): EditableStructField[] {
  return Object.entries(properties).map(([name, rule]) => ({ ...leafFromRule(rule), name }));
}

function leafToRule(
  field: EditableStructField,
):
  | {
      kind: "value_type";
      value_type: string;
      description?: string;
      main_field?: boolean;
      column?: string;
    }
  | {
      kind: "shared_property_type";
      shared_property_type: string;
      description?: string;
      main_field?: boolean;
      column?: string;
    }
  | null {
  let base:
    | { kind: "value_type"; value_type: string }
    | { kind: "shared_property_type"; shared_property_type: string }
    | null = null;
  if (field.leafKind === "value_type" && field.valueType) {
    base = { kind: "value_type", value_type: field.valueType };
  } else if (field.leafKind === "shared_property_type" && field.sharedPropertyType) {
    base = { kind: "shared_property_type", shared_property_type: field.sharedPropertyType };
  }
  if (!base) return null;
  const description = field.description.trim();
  const column = field.column.trim();
  return {
    ...base,
    ...(description ? { description } : {}),
    ...(field.mainField ? { main_field: true } : {}),
    ...(column ? { column } : {}),
  };
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
    base.lifecycleStatus = typeRule?.lifecycle_status ?? "experimental";

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
): Pick<PropertyTypeRule, "editable" | "required" | "visibility" | "render_hints" | "type_classes" | "lifecycle_status"> {
  const flags: Pick<PropertyTypeRule, "editable" | "required" | "visibility" | "render_hints" | "type_classes" | "lifecycle_status"> =
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
  if (prop.lifecycleStatus && prop.lifecycleStatus !== "experimental") {
    flags.lifecycle_status = prop.lifecycleStatus;
  }
  return flags;
}

function serializeStructFields(
  fields: EditableStructField[],
): Record<
  string,
  | { kind: "value_type"; value_type: string; description?: string; main_field?: boolean; column?: string }
  | { kind: "shared_property_type"; shared_property_type: string; description?: string; main_field?: boolean; column?: string }
> | null {
  const properties: Record<
    string,
    | { kind: "value_type"; value_type: string; description?: string; main_field?: boolean; column?: string }
    | { kind: "shared_property_type"; shared_property_type: string; description?: string; main_field?: boolean; column?: string }
  > = {};
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

/** Resolve property_types entry for a payload key (API name or backing column). */
export function resolvePropertyTypeRule(
  key: string,
  propertyTypes?: Record<string, PropertyTypeRule> | null,
  mapping?: Record<string, string> | null,
): PropertyTypeRule | undefined {
  if (!propertyTypes) return undefined;
  if (propertyTypes[key]) return propertyTypes[key];
  if (!mapping) return undefined;
  for (const [apiName, column] of Object.entries(mapping)) {
    if (apiName === key || column === key) return propertyTypes[apiName];
  }
  return undefined;
}

/** Expand a shared-property-type rule into a struct rule when the SPT is struct-typed.
 * Also inherits SPT render_hints / type_classes when the local rule omits them.
 */
export function resolveDisplayTypeRule(
  typeRule: PropertyTypeRule | undefined,
  sharedPropertyTypes: SharedPropertyType[] = [],
): PropertyTypeRule | undefined {
  if (!typeRule) return undefined;
  if (typeRule.kind === "shared_property_type") {
    const spt = sharedPropertyTypes.find((s) => s.api_name === typeRule.shared_property_type);
    if (spt?.struct_properties && Object.keys(spt.struct_properties).length > 0) {
      return {
        kind: "struct",
        properties: spt.struct_properties,
        render_hints: typeRule.render_hints ?? spt.render_hints,
        type_classes: typeRule.type_classes ?? spt.type_classes,
      };
    }
    return {
      ...typeRule,
      render_hints: typeRule.render_hints ?? spt?.render_hints,
      type_classes: typeRule.type_classes ?? spt?.type_classes,
    };
  }
  if (typeRule.kind === "array" && typeRule.element?.kind === "shared_property_type") {
    const element = typeRule.element;
    const spt = sharedPropertyTypes.find((s) => s.api_name === element.shared_property_type);
    if (spt?.struct_properties && Object.keys(spt.struct_properties).length > 0) {
      return {
        kind: "array",
        element: { kind: "struct", properties: spt.struct_properties },
        render_hints: typeRule.render_hints,
        type_classes: typeRule.type_classes,
      };
    }
    // Value-typed SPT element: same render_hints/type_classes inheritance
    // the scalar shared_property_type branch above already applies —
    // without this, an array of e.g. an "identifier"-hinted SPT silently
    // renders with default formatting while the same SPT used as a
    // scalar property correctly inherits the hint.
    return {
      ...typeRule,
      render_hints: typeRule.render_hints ?? spt?.render_hints,
      type_classes: typeRule.type_classes ?? spt?.type_classes,
    };
  }
  return typeRule;
}

/** SPT backing a property key, if any. */
export function lookupSharedPropertyForKey(
  key: string,
  propertyTypes?: Record<string, PropertyTypeRule> | null,
  mapping?: Record<string, string> | null,
  sharedPropertyTypes: SharedPropertyType[] = [],
): SharedPropertyType | undefined {
  const rule = resolvePropertyTypeRule(key, propertyTypes, mapping);
  if (rule?.kind !== "shared_property_type") return undefined;
  return sharedPropertyTypes.find((s) => s.api_name === rule.shared_property_type);
}

/**
 * Foundry inheritance: local visibility wins; otherwise fall back to SPT.
 */
export function effectivePropertyVisibility(
  key: string,
  propertyTypes?: Record<string, PropertyTypeRule> | null,
  mapping?: Record<string, string> | null,
  sharedPropertyTypes: SharedPropertyType[] = [],
): PropertyVisibility {
  const rule = resolvePropertyTypeRule(key, propertyTypes, mapping);
  if (rule?.visibility) return rule.visibility;
  const spt = lookupSharedPropertyForKey(key, propertyTypes, mapping, sharedPropertyTypes);
  return spt?.visibility ?? "normal";
}

/** Display name + aliases for SPT-backed properties (Object Explorer filter / search). */
export function effectivePropertyAliases(
  key: string,
  propertyTypes?: Record<string, PropertyTypeRule> | null,
  mapping?: Record<string, string> | null,
  sharedPropertyTypes: SharedPropertyType[] = [],
): string[] {
  const spt = lookupSharedPropertyForKey(key, propertyTypes, mapping, sharedPropertyTypes);
  if (!spt) return [];
  const terms = [spt.display_name, spt.api_name, ...(spt.aliases ?? [])];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of terms) {
    const term = (raw ?? "").trim();
    if (!term) continue;
    const k = term.toLowerCase();
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(term);
  }
  return out;
}

/** True when property key / SPT aliases match a free-text filter. */
export function propertyMatchesFilter(
  key: string,
  filter: string,
  propertyTypes?: Record<string, PropertyTypeRule> | null,
  mapping?: Record<string, string> | null,
  sharedPropertyTypes: SharedPropertyType[] = [],
): boolean {
  const q = filter.trim().toLowerCase();
  if (!q) return true;
  if (key.toLowerCase().includes(q)) return true;
  return effectivePropertyAliases(key, propertyTypes, mapping, sharedPropertyTypes).some((a) =>
    a.toLowerCase().includes(q),
  );
}

/**
 * Build source-column → format map, filling gaps from SPT.property_format
 * when the ObjectType has no local property_formats entry.
 */
export function buildFormatsBySourceKey(
  propertyFormats: Record<string, PropertyFormatRule> | null | undefined,
  propertyTypes: Record<string, PropertyTypeRule> | null | undefined,
  mapping: Record<string, string> | null | undefined,
  sharedPropertyTypes: SharedPropertyType[] = [],
): Map<string, PropertyFormatRule> {
  const map = new Map<string, PropertyFormatRule>();
  const camelToSnake = (s: string) => s.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);

  for (const [property, rule] of Object.entries(propertyFormats ?? {})) {
    map.set(camelToSnake(property), rule);
    map.set(property, rule);
  }

  for (const [apiName, typeRule] of Object.entries(propertyTypes ?? {})) {
    if (typeRule.kind !== "shared_property_type") continue;
    const column = mapping?.[apiName] ?? camelToSnake(apiName);
    if (map.has(column) || map.has(apiName)) continue;
    const spt = sharedPropertyTypes.find((s) => s.api_name === typeRule.shared_property_type);
    if (spt?.property_format) {
      map.set(column, spt.property_format);
      map.set(apiName, spt.property_format);
    }
  }
  return map;
}

export function sortPropertiesByVisibility(
  keys: string[],
  propertyTypes?: Record<string, PropertyTypeRule> | null,
  mapping?: Record<string, string> | null,
  sharedPropertyTypes: SharedPropertyType[] = [],
): string[] {
  const score = (key: string) => {
    const vis = effectivePropertyVisibility(key, propertyTypes, mapping, sharedPropertyTypes);
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
  sharedPropertyTypes: SharedPropertyType[] = [],
): PropertyVisibility {
  return effectivePropertyVisibility(key, propertyTypes, mapping, sharedPropertyTypes);
}

export function isPropertyHidden(
  key: string,
  propertyTypes?: Record<string, PropertyTypeRule> | null,
  mapping?: Record<string, string> | null,
  sharedPropertyTypes: SharedPropertyType[] = [],
): boolean {
  return resolveVisibility(key, propertyTypes, mapping, sharedPropertyTypes) === "hidden";
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
    lifecycleStatus: "experimental",
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

/** Merge JSON sample keys into struct fields (Foundry Automap-all lite for Holon's JSON column model). */
export function automapStructFieldsFromKeys(
  keys: string[],
  existing: EditableStructField[],
  defaultValueType = "String",
): EditableStructField[] {
  const seen = new Set(existing.map((f) => f.name.trim()).filter(Boolean));
  const next = [...existing];
  for (const raw of keys) {
    const name = String(raw).trim();
    if (!name || seen.has(name) || name.includes(".")) continue;
    seen.add(name);
    next.push({
      ...emptyStructField(name),
      leafKind: "value_type",
      valueType: defaultValueType,
    });
  }
  return next.length > 0 ? next : [emptyStructField("field1")];
}

/** Foundry-style: set each field's backing column to the field API name when empty. */
export function mapStructFieldColumnsByName(fields: EditableStructField[]): EditableStructField[] {
  return fields.map((f) => {
    const name = f.name.trim();
    if (!name || f.column.trim()) return f;
    return { ...f, column: name };
  });
}

/** Strip OT-local column mappings before promoting a struct to an SPT. */
export function stripStructFieldColumns<T extends { column?: string }>(
  properties: Record<string, T>,
): Record<string, Omit<T, "column">> {
  const out: Record<string, Omit<T, "column">> = {};
  for (const [name, rule] of Object.entries(properties)) {
    const { column: _column, ...rest } = rule;
    out[name] = rest;
  }
  return out;
}

/** Collect union of object keys from sample struct values (dicts, JSON strings, or arrays of those). */
export function collectStructSampleKeys(samples: unknown[]): string[] {
  const keys = new Set<string>();
  const visit = (value: unknown) => {
    let current = value;
    if (typeof current === "string") {
      try {
        current = JSON.parse(current);
      } catch {
        return;
      }
    }
    if (Array.isArray(current)) {
      for (const item of current) visit(item);
      return;
    }
    if (current && typeof current === "object") {
      for (const key of Object.keys(current as Record<string, unknown>)) {
        if (key && !key.includes(".")) keys.add(key);
      }
    }
  };
  for (const sample of samples) visit(sample);
  return [...keys].sort();
}

/** Build editable struct fields from a published `properties` map (local struct or SPT). */
export function editableStructFieldsFromProperties(
  properties: Record<string, { kind?: string; value_type?: string; shared_property_type?: string; description?: string; main_field?: boolean }>,
): EditableStructField[] {
  const fields = structFieldsFromRule(properties);
  return fields.length > 0 ? fields : [emptyStructField("field1")];
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

/** Foundry aliases — preserve case; comma/newline separated. */
export function parseAliasesInput(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(/[,\n]+/)) {
    const term = part.trim();
    if (!term) continue;
    const key = term.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(term);
  }
  return out;
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
