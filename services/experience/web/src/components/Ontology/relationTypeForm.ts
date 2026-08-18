import type { RelationType } from "../../api/knowledge";
import { parseTypeClassesInput } from "./typeClassUtils";
import { urnShortName } from "../ObjectExplorer/objectExplorerUtils";

export const CREATE_STEPS = ["Ends", "Storage", "Side names", "Governance"] as const;

export interface RelationTypeFormState {
  name: string;
  sourceObjectType: string;
  targetObjectType: string;
  sourceProperty: string;
  targetProperty: string;
  cardinality: string;
  storageKind: string;
  joinDatasetUrn: string;
  joinSourceColumn: string;
  joinTargetColumn: string;
  midObjectType: string;
  midSourceProperty: string;
  midTargetProperty: string;
  sourceDisplayName: string;
  sourcePluralDisplayName: string;
  sourceApiName: string;
  sourceVisibility: string;
  targetDisplayName: string;
  targetPluralDisplayName: string;
  targetApiName: string;
  targetVisibility: string;
  lifecycleStatus: string;
  deprecationReason: string;
  deprecationDeadline: string;
  replacementUrn: string;
  typeClasses: string;
  projectUrn: string;
}

export const DEFAULT_RELATION_TYPE_FORM: RelationTypeFormState = {
  name: "",
  sourceObjectType: "",
  targetObjectType: "",
  sourceProperty: "",
  targetProperty: "",
  cardinality: "many_to_one",
  storageKind: "foreign_key",
  joinDatasetUrn: "",
  joinSourceColumn: "",
  joinTargetColumn: "",
  midObjectType: "",
  midSourceProperty: "",
  midTargetProperty: "",
  sourceDisplayName: "",
  sourcePluralDisplayName: "",
  sourceApiName: "",
  sourceVisibility: "normal",
  targetDisplayName: "",
  targetPluralDisplayName: "",
  targetApiName: "",
  targetVisibility: "normal",
  lifecycleStatus: "experimental",
  deprecationReason: "",
  deprecationDeadline: "",
  replacementUrn: "",
  typeClasses: "",
  projectUrn: "",
};

function optional(value: string): string | undefined {
  return value || undefined;
}

function deprecationFields(form: RelationTypeFormState) {
  if (form.lifecycleStatus !== "deprecated") {
    return {
      deprecation_reason: undefined as string | undefined,
      deprecation_deadline: undefined as string | undefined,
      replacement_urn: undefined as string | undefined,
    };
  }
  return {
    deprecation_reason: optional(form.deprecationReason),
    deprecation_deadline: optional(form.deprecationDeadline),
    replacement_urn: optional(form.replacementUrn),
  };
}

export function relationTypeFormFromRecord(rt: RelationType): RelationTypeFormState {
  return {
    ...DEFAULT_RELATION_TYPE_FORM,
    name: rt.name,
    sourceObjectType: urnShortName(rt.source_object_type_urn),
    targetObjectType: urnShortName(rt.target_object_type_urn),
    sourceProperty: rt.source_property ?? "",
    targetProperty: rt.target_property ?? "",
    cardinality: rt.cardinality,
    storageKind: rt.storage_kind ?? "foreign_key",
    joinDatasetUrn: rt.join_dataset_urn ?? "",
    joinSourceColumn: rt.join_source_column ?? "",
    joinTargetColumn: rt.join_target_column ?? "",
    midObjectType: rt.mid_object_type_urn ? urnShortName(rt.mid_object_type_urn) : "",
    midSourceProperty: rt.mid_source_property ?? "",
    midTargetProperty: rt.mid_target_property ?? "",
    sourceDisplayName: rt.source_display_name ?? "",
    sourcePluralDisplayName: rt.source_plural_display_name ?? "",
    sourceApiName: rt.source_api_name || rt.name.split(".").at(-1) || "",
    sourceVisibility: rt.source_visibility ?? "normal",
    targetDisplayName: rt.target_display_name ?? "",
    targetPluralDisplayName: rt.target_plural_display_name ?? "",
    targetApiName: rt.target_api_name || rt.target_property || "",
    targetVisibility: rt.target_visibility ?? "normal",
    lifecycleStatus: rt.lifecycle_status ?? "experimental",
    deprecationReason: rt.deprecation_reason ?? "",
    deprecationDeadline: (rt.deprecation_deadline ?? "").toString().slice(0, 10),
    replacementUrn: rt.replacement_urn ?? "",
    typeClasses: (rt.type_classes ?? []).join(", "),
    projectUrn: rt.project_urn ?? "",
  };
}

export function isRelationTypeCreateStepValid(form: RelationTypeFormState, step: number): boolean {
  if (step === 0) return !!form.name.trim() && !!form.sourceObjectType && !!form.targetObjectType;
  if (step === 1) {
    if (form.storageKind === "foreign_key") return !!form.sourceProperty.trim();
    if (form.storageKind === "join_dataset") {
      return !!form.joinDatasetUrn.trim() && !!form.joinSourceColumn.trim() && !!form.joinTargetColumn.trim();
    }
    return !!form.midObjectType && !!form.midSourceProperty.trim() && !!form.midTargetProperty.trim();
  }
  return true;
}

export function defaultJoinDatasetName(sourceObjectType: string, targetObjectType: string): string {
  return `${sourceObjectType || "source"}_${targetObjectType || "target"}_bridge`;
}

export function defaultJoinSourceColumn(sourceObjectType: string): string {
  return `${(sourceObjectType || "source").toLowerCase()}_id`;
}

export function defaultJoinTargetColumn(targetObjectType: string): string {
  return `${(targetObjectType || "target").toLowerCase()}_id`;
}

export function relationTypeCreateBody(form: RelationTypeFormState) {
  return {
    name: form.name,
    source_object_type: form.sourceObjectType,
    target_object_type: form.targetObjectType,
    source_property: form.sourceProperty,
    target_property: form.targetProperty,
    cardinality: form.cardinality,
    storage_kind: form.storageKind,
    join_dataset_urn: optional(form.joinDatasetUrn),
    join_source_column: optional(form.joinSourceColumn),
    join_target_column: optional(form.joinTargetColumn),
    mid_object_type: optional(form.midObjectType),
    mid_source_property: optional(form.midSourceProperty),
    mid_target_property: optional(form.midTargetProperty),
    source_display_name: optional(form.sourceDisplayName),
    source_plural_display_name: optional(form.sourcePluralDisplayName),
    source_api_name: optional(form.sourceApiName),
    source_visibility: form.sourceVisibility,
    target_display_name: optional(form.targetDisplayName),
    target_plural_display_name: optional(form.targetPluralDisplayName),
    target_api_name: optional(form.targetApiName),
    target_visibility: form.targetVisibility,
    lifecycle_status: form.lifecycleStatus,
    type_classes: parseTypeClassesInput(form.typeClasses),
    project_urn: optional(form.projectUrn),
    ...deprecationFields(form),
  };
}

export function relationTypeUpdateBody(form: RelationTypeFormState, previousProjectUrn: string) {
  return {
    target_property: form.targetProperty,
    cardinality: form.cardinality,
    storage_kind: form.storageKind,
    join_dataset_urn: optional(form.joinDatasetUrn),
    join_source_column: optional(form.joinSourceColumn),
    join_target_column: optional(form.joinTargetColumn),
    mid_object_type: optional(form.midObjectType),
    mid_source_property: optional(form.midSourceProperty),
    mid_target_property: optional(form.midTargetProperty),
    source_display_name: form.sourceDisplayName,
    source_plural_display_name: form.sourcePluralDisplayName,
    source_api_name: form.sourceApiName,
    source_visibility: form.sourceVisibility,
    target_display_name: form.targetDisplayName,
    target_plural_display_name: form.targetPluralDisplayName,
    target_api_name: form.targetApiName,
    target_visibility: form.targetVisibility,
    lifecycle_status: form.lifecycleStatus,
    type_classes: parseTypeClassesInput(form.typeClasses),
    project_urn: optional(form.projectUrn),
    clear_project_urn: !form.projectUrn && !!previousProjectUrn,
    ...deprecationFields(form),
  };
}

export function relationTypeBranchDefinition(rt: RelationType) {
  return {
    source_object_type: urnShortName(rt.source_object_type_urn),
    target_object_type: urnShortName(rt.target_object_type_urn),
    source_object_type_urn: rt.source_object_type_urn,
    target_object_type_urn: rt.target_object_type_urn,
    source_property: rt.source_property,
    target_property: rt.target_property,
    cardinality: rt.cardinality,
    storage_kind: rt.storage_kind ?? "foreign_key",
    join_dataset_urn: rt.join_dataset_urn ?? null,
    join_source_column: rt.join_source_column ?? null,
    join_target_column: rt.join_target_column ?? null,
    mid_object_type_urn: rt.mid_object_type_urn ?? null,
    mid_object_type: rt.mid_object_type_urn ? urnShortName(rt.mid_object_type_urn) : null,
    mid_source_property: rt.mid_source_property ?? null,
    mid_target_property: rt.mid_target_property ?? null,
    source_display_name: rt.source_display_name ?? "",
    source_plural_display_name: rt.source_plural_display_name ?? "",
    source_api_name: rt.source_api_name ?? "",
    source_visibility: rt.source_visibility ?? "normal",
    target_display_name: rt.target_display_name ?? "",
    target_plural_display_name: rt.target_plural_display_name ?? "",
    target_api_name: rt.target_api_name ?? "",
    target_visibility: rt.target_visibility ?? "normal",
    lifecycle_status: rt.lifecycle_status ?? "experimental",
    type_classes: rt.type_classes ?? [],
    project_urn: rt.project_urn ?? null,
  };
}
