import type { ObjectType } from "../../api/knowledge";
import {
  buildEditableProperties,
  serializePropertyEditor,
  type EditableProperty,
} from "./propertyEditorUtils";
import {
  buildEditableDerived,
  serializeDerivedProperties,
  type EditableDerivedProperty,
} from "./derivedEditorUtils";
import { urnShortName } from "../ObjectExplorer/objectExplorerUtils";

export type ObjectTypeDraftStep =
  | "overview"
  | "identity"
  | "properties"
  | "derived"
  | "datasources"
  | "advanced"
  | "versions";

export function toggleSetValue(set: Set<string>, value: string): Set<string> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

export function patchNestedBinding(
  prev: Record<string, Record<string, string>>,
  ifaceName: string,
  key: string,
  value: string | undefined,
): Record<string, Record<string, string>> {
  const nextIface = { ...(prev[ifaceName] ?? {}) };
  if (!value) delete nextIface[key];
  else nextIface[key] = value;
  const next = { ...prev };
  if (Object.keys(nextIface).length === 0) delete next[ifaceName];
  else next[ifaceName] = nextIface;
  return next;
}

export function patchInterfacePropertyBinding(
  prev: Record<string, Record<string, string>>,
  ifaceName: string,
  propName: string,
  rawValue: string,
): Record<string, Record<string, string>> {
  const value = rawValue.trim();
  if (!value || value === propName) return patchNestedBinding(prev, ifaceName, propName, undefined);
  return patchNestedBinding(prev, ifaceName, propName, value);
}

export function relationTypesTouchingObjectType<T extends { source_object_type_urn: string; target_object_type_urn: string }>(
  relationTypes: T[],
  objectTypeName: string,
): T[] {
  return relationTypes.filter((rt) => {
    return (
      urnShortName(rt.source_object_type_urn) === objectTypeName ||
      urnShortName(rt.target_object_type_urn) === objectTypeName
    );
  });
}

export type ObjectTypeProposeInput = {
  conditionalFormatsJson: string;
  properties: EditableProperty[];
  derivedProperties: EditableDerivedProperty[];
  primaryKey: string;
  titleKey: string;
  description: string;
  implements: Iterable<string>;
  linkConstraintBindings: Record<string, Record<string, string>>;
  interfacePropertyBindings: Record<string, Record<string, string>>;
  markings: Iterable<string>;
  projectUrn: string;
  pluralDisplayName: string;
  lifecycleStatus: string;
  deprecationReason: string;
  deprecationDeadline: string;
  replacementUrn: string;
  visibility: string;
  icon: string;
};

export type ObjectTypeProposeBody = ReturnType<typeof buildProposeBody>;

export type ObjectTypeDraftForm = Omit<ObjectTypeProposeInput, "implements" | "markings"> & {
  implements: Set<string>;
  markings: Set<string>;
};

export function objectTypeDraftFormFromRecord(objectType: ObjectType): ObjectTypeDraftForm {
  return {
    conditionalFormatsJson: JSON.stringify(objectType.conditional_formats ?? {}, null, 2),
    properties: buildEditableProperties(
      objectType.property_mapping ?? {},
      objectType.property_types ?? {},
      objectType.property_formats ?? {},
    ),
    derivedProperties: buildEditableDerived(objectType.derived_properties ?? {}),
    primaryKey: objectType.primary_key ?? "id",
    titleKey: objectType.title_key ?? "",
    description: objectType.description,
    implements: new Set(objectType.implements ?? []),
    linkConstraintBindings: objectType.link_constraint_bindings ?? {},
    interfacePropertyBindings: objectType.interface_property_bindings ?? {},
    markings: new Set(objectType.markings ?? []),
    projectUrn: objectType.project_urn ?? "",
    pluralDisplayName: objectType.plural_display_name ?? "",
    lifecycleStatus: objectType.lifecycle_status ?? "experimental",
    deprecationReason: objectType.deprecation_reason ?? "",
    deprecationDeadline: (objectType.deprecation_deadline ?? "").toString().slice(0, 10),
    replacementUrn: objectType.replacement_urn ?? "",
    visibility: objectType.visibility ?? "normal",
    icon: objectType.icon ?? "",
  };
}

export function prepareObjectTypePropose(
  input: ObjectTypeProposeInput,
): { ok: true; body: ObjectTypeProposeBody } | { ok: false; error: string; step: ObjectTypeDraftStep } {
  let conditional_formats: unknown;
  try {
    conditional_formats = JSON.parse(input.conditionalFormatsJson);
  } catch {
    return { ok: false, error: "Conditional formats must be valid JSON.", step: "advanced" };
  }
  const { property_mapping, property_types, property_formats } = serializePropertyEditor(input.properties);
  if (Object.keys(property_mapping).length === 0) {
    return { ok: false, error: "At least one property with an API name and backing column is required.", step: "properties" };
  }
  if (!property_mapping[input.primaryKey]) {
    return {
      ok: false,
      error: `Primary key "${input.primaryKey}" must be one of the mapped properties.`,
      step: "identity",
    };
  }
  if (input.titleKey && !property_mapping[input.titleKey]) {
    return {
      ok: false,
      error: `Title key "${input.titleKey}" must be one of the mapped properties.`,
      step: "identity",
    };
  }
  return {
    ok: true,
    body: buildProposeBody(input, {
      conditional_formats: conditional_formats as ObjectType["conditional_formats"],
      property_mapping,
      property_types,
      property_formats,
      derived_properties: serializeDerivedProperties(input.derivedProperties),
    }),
  };
}

function buildProposeBody(
  input: ObjectTypeProposeInput,
  serialized: {
    conditional_formats: ObjectType["conditional_formats"];
    property_mapping: Record<string, string>;
    property_types: ReturnType<typeof serializePropertyEditor>["property_types"];
    property_formats: ReturnType<typeof serializePropertyEditor>["property_formats"];
    derived_properties: ReturnType<typeof serializeDerivedProperties>;
  },
) {
  const deprecated = input.lifecycleStatus === "deprecated";
  return {
    description: input.description,
    implements: [...input.implements],
    link_constraint_bindings: input.linkConstraintBindings,
    interface_property_bindings: input.interfacePropertyBindings,
    markings: [...input.markings],
    property_mapping: serialized.property_mapping,
    property_types: serialized.property_types,
    derived_properties: serialized.derived_properties,
    property_formats: serialized.property_formats,
    conditional_formats: serialized.conditional_formats,
    project_urn: input.projectUrn || undefined,
    primary_key: input.primaryKey,
    title_key: input.titleKey || null,
    plural_display_name: input.pluralDisplayName,
    lifecycle_status: input.lifecycleStatus,
    deprecation_reason: deprecated ? input.deprecationReason : null,
    deprecation_deadline: deprecated ? input.deprecationDeadline || null : null,
    replacement_urn: deprecated ? input.replacementUrn || null : null,
    visibility: input.visibility,
    icon: input.icon || null,
  };
}
