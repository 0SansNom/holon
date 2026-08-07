import { KNOWLEDGE_URL } from "./config";
import { api } from "./client";

export type PropertyFormatRule =
  | { kind: "currency"; currency: string }
  | { kind: "badge"; colors: Record<string, string> };

// A leaf/struct/array declaration — one level of struct/array nesting
// only, the same limit `ontology/publishing.py`'s `_validate_property_types`
// enforces server-side (a struct's own properties, or an array's
// element, may only ever be a `value_type`/`shared_property_type` leaf).
type PropertyTypeLeaf =
  | { kind: "value_type"; value_type: string }
  | { kind: "shared_property_type"; shared_property_type: string };

export type PropertyTypeRule =
  | PropertyTypeLeaf
  | { kind: "struct"; properties: Record<string, PropertyTypeLeaf> }
  | { kind: "array"; element: PropertyTypeLeaf };

export interface ObjectType {
  urn: string;
  tenant_id: string;
  name: string;
  source_dataset_urn: string;
  property_mapping: Record<string, string>;
  property_formats: Record<string, PropertyFormatRule>;
  property_types?: Record<string, PropertyTypeRule>;
  implements?: string[];
  derived_properties?: Record<string, string>;
  markings?: string[];
  classification: "public" | "internal" | "confidential" | "restricted";
  description: string;
  version: number;
  created_at: string;
  column_classification?: Record<string, string>;
}

export interface ValueType {
  tenant_id: string;
  name: string;
  base_type: "string" | "integer" | "double" | "boolean" | "date" | "timestamp";
  format_regex: string | null;
  description: string;
  created_at: string;
}

export interface SharedPropertyType {
  tenant_id: string;
  api_name: string;
  display_name: string;
  value_type: string;
  description: string;
  created_at: string;
}

export interface ActionParameter {
  name: string;
  value_type: string;
  required: boolean;
}

export interface ActionEdit {
  property: string;
  source: "parameter" | "literal";
  parameter_name?: string;
  value?: unknown;
}

export interface SubmissionCriterion {
  property: string;
  operator: "eq" | "neq" | "gt" | "gte" | "lt" | "lte";
  value: unknown;
}

export interface ActionType {
  tenant_id: string;
  name: string;
  target_object_type: string;
  required_permission: string;
  risk_level: "low" | "high";
  description: string;
  parameters: ActionParameter[];
  edits: ActionEdit[];
  submission_criteria: SubmissionCriterion[];
  function_side_effect: string | null;
  writeback_dataset: string | null;
  created_at: string;
}

export interface InterfaceType {
  tenant_id: string;
  name: string;
  required_properties: string[];
  required_actions: string[];
  description: string;
  created_at: string;
}

export interface Marking {
  tenant_id: string;
  name: string;
  description: string;
  created_at: string;
}

export interface ObjectTypeVersion {
  id: number;
  object_type_urn: string;
  tenant_id: string;
  version: number;
  property_mapping: Record<string, string>;
  description: string;
  status: "draft" | "published";
  created_at: string;
  published_at: string | null;
}

export interface ActionDefinition {
  name: string;
  target_object_type: string;
  required_permission: string;
  risk_level: "low" | "high";
  description: string;
  function_side_effect?: string | null;
  writeback_dataset?: string | null;
  // Only ever present for a declarative Action Type — the two hardcoded
  // Customer Actions never carry this key at all (see `libs/holon_osdk/
  // schema.py`'s `is_declarative` detection, which relies on exactly
  // this presence-vs-absence distinction).
  parameters?: ActionParameter[];
}

export interface LineageEdge {
  source_urn: string;
  target_urn: string;
  relation: string;
  source_column: string;
  target_property: string;
}

export interface SearchResult {
  total: number;
  results: Array<{
    urn: string;
    object_type: string;
    tenant_id: string;
    classification: string;
    text: string;
  }>;
  facets: Record<string, number>;
}

export interface GlossaryTerm {
  term: string;
  definition: string;
  synonyms: string[];
  related_object_type_urn: string | null;
}

export interface InstanceGraphNode {
  id: string;
  objectType: string;
  instanceId: string | number;
  label: string;
  hop: number;
  degraded: boolean;
  maskedFields: string[];
}

export interface InstanceGraphEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  direction: "toward_one" | "toward_many";
}

export interface InstanceGraph {
  root: string;
  nodes: InstanceGraphNode[];
  edges: InstanceGraphEdge[];
  truncated: boolean;
}

export interface RelationType {
  urn: string;
  name: string;
  source_object_type_urn: string;
  target_object_type_urn: string;
  source_property: string;
  cardinality: string;
}

export interface DatasetPreviewColumn {
  name: string;
  sample: unknown;
}

export const knowledgeApi = {
  listObjectTypes: () => api.get<ObjectType[]>(`${KNOWLEDGE_URL}/ontology`),
  getObjectType: (name: string) => api.get<ObjectType>(`${KNOWLEDGE_URL}/ontology/${name}`),
  previewDataset: (datasetName: string) =>
    api.get<{ columns: DatasetPreviewColumn[] }>(`${KNOWLEDGE_URL}/catalog/datasets/${datasetName}/preview`),
  createObjectType: (body: {
    name: string;
    source_dataset_urn: string;
    property_mapping: Record<string, string>;
    description?: string;
    column_classification?: Record<string, string>;
  }) => api.post<ObjectType>(`${KNOWLEDGE_URL}/object-types`, body),
  listObjectTypeVersions: (name: string) => api.get<ObjectTypeVersion[]>(`${KNOWLEDGE_URL}/ontology/${name}/versions`),
  proposeObjectTypeVersion: (
    name: string,
    body: {
      description?: string;
      property_mapping?: Record<string, string>;
      property_formats?: Record<string, PropertyFormatRule>;
      property_types?: Record<string, PropertyTypeRule>;
      implements?: string[];
      derived_properties?: Record<string, string>;
      markings?: string[];
    },
  ) => api.post<ObjectTypeVersion>(`${KNOWLEDGE_URL}/ontology/${name}/versions`, body),
  publishObjectTypeVersion: (name: string, version: number) =>
    api.post<ObjectType>(`${KNOWLEDGE_URL}/ontology/${name}/versions/${version}/publish`),

  listObjects: (objectType: string) => api.get<Array<Record<string, unknown>>>(`${KNOWLEDGE_URL}/objects/${objectType}`),
  getObject: (objectType: string, id: string | number) =>
    api.get<Record<string, unknown>>(`${KNOWLEDGE_URL}/objects/${objectType}/${id}`),

  listActions: () => api.get<ActionDefinition[]>(`${KNOWLEDGE_URL}/actions`),
  invokeAction: (objectType: string, id: string | number, localActionName: string, reason: string) =>
    api.post<Record<string, unknown>>(`${KNOWLEDGE_URL}/objects/${objectType}/${id}/actions/${localActionName}`, { reason }),

  getLineage: (urn: string) => api.get<LineageEdge[]>(`${KNOWLEDGE_URL}/lineage/${encodeURIComponent(urn)}`),

  getObjectGraph: (objectType: string, id: string | number, hops = 2) =>
    api.get<InstanceGraph>(`${KNOWLEDGE_URL}/objects/${objectType}/${encodeURIComponent(String(id))}/graph?hops=${hops}`),

  search: (q: string, options?: { objectType?: string; from?: number; size?: number }) => {
    const params = new URLSearchParams({ q });
    if (options?.objectType) params.set("object_type", options.objectType);
    if (options?.from) params.set("from", String(options.from));
    if (options?.size) params.set("size", String(options.size));
    return api.get<SearchResult>(`${KNOWLEDGE_URL}/search?${params.toString()}`);
  },

  listGlossary: () => api.get<GlossaryTerm[]>(`${KNOWLEDGE_URL}/glossary`),

  execute: (body: { object_type: string; filter_property: string; filter_value: string; operation?: "filter" | "count" }) =>
    api.post<{ planId: string; planHash: string; cached: boolean; rowCount?: number; count?: number; results?: unknown[] }>(
      `${KNOWLEDGE_URL}/execute`,
      body,
    ),

  listRelationTypes: () => api.get<RelationType[]>(`${KNOWLEDGE_URL}/relation-types`),
  createRelationType: (body: {
    name: string;
    source_object_type: string;
    target_object_type: string;
    source_property: string;
    cardinality: string;
  }) => api.post<RelationType>(`${KNOWLEDGE_URL}/relation-types`, body),

  listValueTypes: () => api.get<ValueType[]>(`${KNOWLEDGE_URL}/value-types`),
  createValueType: (body: { name: string; base_type: string; format_regex?: string; description?: string }) =>
    api.post<ValueType>(`${KNOWLEDGE_URL}/value-types`, body),

  listSharedPropertyTypes: () => api.get<SharedPropertyType[]>(`${KNOWLEDGE_URL}/shared-property-types`),
  createSharedPropertyType: (body: { api_name: string; display_name: string; value_type: string; description?: string }) =>
    api.post<SharedPropertyType>(`${KNOWLEDGE_URL}/shared-property-types`, body),

  listActionTypes: () => api.get<ActionType[]>(`${KNOWLEDGE_URL}/action-types`),
  createActionType: (body: {
    name: string;
    target_object_type: string;
    required_permission: string;
    risk_level: "low" | "high";
    description: string;
    parameters?: ActionParameter[];
    edits: ActionEdit[];
    submission_criteria?: SubmissionCriterion[];
    function_side_effect?: string;
    writeback_dataset?: string;
  }) => api.post<ActionType>(`${KNOWLEDGE_URL}/action-types`, body),

  listInterfaces: () => api.get<InterfaceType[]>(`${KNOWLEDGE_URL}/interfaces`),
  createInterface: (body: { name: string; required_properties?: string[]; required_actions?: string[]; description?: string }) =>
    api.post<InterfaceType>(`${KNOWLEDGE_URL}/interfaces`, body),

  listMarkings: () => api.get<Marking[]>(`${KNOWLEDGE_URL}/markings`),
  createMarking: (body: { name: string; description?: string }) => api.post<Marking>(`${KNOWLEDGE_URL}/markings`, body),
};
