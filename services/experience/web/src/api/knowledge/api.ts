import { KNOWLEDGE_URL } from "../config";
import { api } from "../client";
import type {
  ObjectType,
  ObjectTypeVersion,
  ObjectTypeGroup,
  ObjectTypeBranchFields,
  Branch,
  BranchReview,
  ResourceBranchKind,
  ActionDefinition,
  ActionType,
  ActionParameter,
  ActionEdit,
  ActionParameterSection,
  SubmissionCriterion,
  InterfaceType,
  Marking,
  ValueType,
  ValueTypeConstraint,
  SharedPropertyType,
  RelationType,
  PropertyFormatRule,
  ConditionalFormatRule,
  PropertyTypeRule,
  DerivedPropertyValue,
  LineageEdge,
  InstanceGraph,
  TimelineEvent,
  ObjectLinksResponse,
  SearchResult,
  GlossaryTerm,
  DatasetPreviewColumn,
  HealthCheckFinding,
} from "./types";

export const knowledgeApi = {
  listObjectTypes: () => api.get<ObjectType[]>(`${KNOWLEDGE_URL}/ontology`),
  getOntologyHealthCheck: () => api.get<HealthCheckFinding[]>(`${KNOWLEDGE_URL}/ontology/health-check`),
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

  listBranches: (name: string) => api.get<Branch[]>(`${KNOWLEDGE_URL}/ontology/${name}/branches`),
  getBranch: (name: string, branchName: string) => api.get<Branch>(`${KNOWLEDGE_URL}/ontology/${name}/branches/${branchName}`),
  createBranch: (name: string, body: { branch_name: string } & ObjectTypeBranchFields) =>
    api.post<Branch>(`${KNOWLEDGE_URL}/ontology/${name}/branches`, body),
  updateBranchDraft: (name: string, branchName: string, body: ObjectTypeBranchFields) =>
    api.post<Branch>(`${KNOWLEDGE_URL}/ontology/${name}/branches/${branchName}/draft`, body),
  reviewBranch: (name: string, branchName: string, body: { decision: "approved" | "changes_requested"; note?: string }) =>
    api.post<Branch>(`${KNOWLEDGE_URL}/ontology/${name}/branches/${branchName}/review`, body),
  listBranchReviews: (name: string, branchName: string) =>
    api.get<BranchReview[]>(`${KNOWLEDGE_URL}/ontology/${name}/branches/${branchName}/reviews`),

  listResourceBranches: (resourceType: ResourceBranchKind, resourceName: string) =>
    api.get<Branch[]>(`${KNOWLEDGE_URL}/ontology-resources/${resourceType}/${resourceName}/branches`),
  getResourceBranch: (resourceType: ResourceBranchKind, resourceName: string, branchName: string) =>
    api.get<Branch>(`${KNOWLEDGE_URL}/ontology-resources/${resourceType}/${resourceName}/branches/${branchName}`),
  createResourceBranch: (resourceType: ResourceBranchKind, resourceName: string, body: { branch_name: string; proposed_definition: object }) =>
    api.post<Branch>(`${KNOWLEDGE_URL}/ontology-resources/${resourceType}/${resourceName}/branches`, body),
  updateResourceBranchDraft: (resourceType: ResourceBranchKind, resourceName: string, branchName: string, body: { proposed_definition: object }) =>
    api.post<Branch>(`${KNOWLEDGE_URL}/ontology-resources/${resourceType}/${resourceName}/branches/${branchName}/draft`, body),
  reviewResourceBranch: (
    resourceType: ResourceBranchKind,
    resourceName: string,
    branchName: string,
    body: { decision: "approved" | "changes_requested"; note?: string },
  ) => api.post<Branch>(`${KNOWLEDGE_URL}/ontology-resources/${resourceType}/${resourceName}/branches/${branchName}/review`, body),
  listResourceBranchReviews: (resourceType: ResourceBranchKind, resourceName: string, branchName: string) =>
    api.get<BranchReview[]>(`${KNOWLEDGE_URL}/ontology-resources/${resourceType}/${resourceName}/branches/${branchName}/reviews`),

  proposeObjectTypeVersion: (
    name: string,
    body: {
      description?: string;
      property_mapping?: Record<string, string>;
      property_formats?: Record<string, PropertyFormatRule>;
      conditional_formats?: Record<string, ConditionalFormatRule[]>;
      property_types?: Record<string, PropertyTypeRule>;
      implements?: string[];
      derived_properties?: Record<string, DerivedPropertyValue>;
      markings?: string[];
      project_urn?: string;
    },
  ) => api.post<ObjectTypeVersion>(`${KNOWLEDGE_URL}/ontology/${name}/versions`, body),
  publishObjectTypeVersion: (name: string, version: number) =>
    api.post<ObjectType>(`${KNOWLEDGE_URL}/ontology/${name}/versions/${version}/publish`),

  listObjects: (objectType: string) => api.get<Array<Record<string, unknown>>>(`${KNOWLEDGE_URL}/objects/${objectType}`),
  getObject: (objectType: string, id: string | number) =>
    api.get<Record<string, unknown>>(`${KNOWLEDGE_URL}/objects/${objectType}/${id}`),

  listActions: () => api.get<ActionDefinition[]>(`${KNOWLEDGE_URL}/actions`),
  invokeAction: (
    objectType: string,
    id: string | number,
    localActionName: string,
    reason: string,
    parameters?: Record<string, unknown>,
  ) =>
    api.post<Record<string, unknown>>(`${KNOWLEDGE_URL}/objects/${objectType}/${id}/actions/${localActionName}`, {
      reason,
      parameters: parameters ?? {},
    }),

  revertActionInvocation: (invocationId: number) =>
    api.post<Record<string, unknown>>(`${KNOWLEDGE_URL}/action-invocations/${invocationId}/revert`),

  getLineage: (urn: string) => api.get<LineageEdge[]>(`${KNOWLEDGE_URL}/lineage/${encodeURIComponent(urn)}`),

  getObjectGraph: (objectType: string, id: string | number, hops = 2) =>
    api.get<InstanceGraph>(`${KNOWLEDGE_URL}/objects/${objectType}/${encodeURIComponent(String(id))}/graph?hops=${hops}`),

  getObjectTimeline: (objectType: string, id: string | number) =>
    api.get<TimelineEvent[]>(`${KNOWLEDGE_URL}/objects/${objectType}/${encodeURIComponent(String(id))}/timeline`),

  search: (q: string, options?: { objectType?: string; from?: number; size?: number }) => {
    const params = new URLSearchParams({ q });
    if (options?.objectType) params.set("object_type", options.objectType);
    if (options?.from !== undefined) params.set("from", String(options.from));
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
    target_property: string;
    cardinality: string;
  }) => api.post<RelationType>(`${KNOWLEDGE_URL}/relation-types`, body),
  updateRelationType: (name: string, body: { target_property?: string; cardinality?: string }) =>
    api.put<RelationType>(`${KNOWLEDGE_URL}/relation-types/${name}`, body),

  getObjectLinks: (objectType: string, id: string | number, linkName: string) =>
    api.get<ObjectLinksResponse>(
      `${KNOWLEDGE_URL}/objects/${objectType}/${encodeURIComponent(String(id))}/links/${encodeURIComponent(linkName)}`,
    ),

  listObjectTypeGroups: () => api.get<ObjectTypeGroup[]>(`${KNOWLEDGE_URL}/object-type-groups`),
  createObjectTypeGroup: (body: { name: string; description?: string; object_types: string[] }) =>
    api.post<ObjectTypeGroup>(`${KNOWLEDGE_URL}/object-type-groups`, body),

  listValueTypes: () => api.get<ValueType[]>(`${KNOWLEDGE_URL}/value-types`),
  createValueType: (body: {
    name: string;
    base_type: string;
    format_regex?: string;
    constraints?: ValueTypeConstraint[];
    description?: string;
  }) => api.post<ValueType>(`${KNOWLEDGE_URL}/value-types`, body),
  updateValueType: (
    name: string,
    body: { format_regex?: string | null; constraints?: ValueTypeConstraint[]; description?: string },
  ) => api.put<ValueType>(`${KNOWLEDGE_URL}/value-types/${name}`, body),

  listSharedPropertyTypes: () => api.get<SharedPropertyType[]>(`${KNOWLEDGE_URL}/shared-property-types`),
  createSharedPropertyType: (body: { api_name: string; display_name: string; value_type: string; description?: string }) =>
    api.post<SharedPropertyType>(`${KNOWLEDGE_URL}/shared-property-types`, body),
  updateSharedPropertyType: (apiName: string, body: { display_name?: string; description?: string }) =>
    api.put<SharedPropertyType>(`${KNOWLEDGE_URL}/shared-property-types/${apiName}`, body),

  listActionTypes: () => api.get<ActionType[]>(`${KNOWLEDGE_URL}/action-types`),
  createActionType: (body: {
    name: string;
    target_object_type?: string;
    target_interface?: string;
    required_permission: string;
    risk_level: "low" | "high";
    description: string;
    parameters?: ActionParameter[];
    edits?: ActionEdit[];
    submission_criteria?: SubmissionCriterion[];
    function_side_effect?: string;
    writeback_dataset?: string;
    edit_function?: string;
    sections?: ActionParameterSection[];
  }) => api.post<ActionType>(`${KNOWLEDGE_URL}/action-types`, body),
  updateActionType: (
    name: string,
    body: {
      name: string;
      target_object_type?: string;
      target_interface?: string;
      required_permission: string;
      risk_level: "low" | "high";
      description: string;
      parameters?: ActionParameter[];
      edits?: ActionEdit[];
      submission_criteria?: SubmissionCriterion[];
      function_side_effect?: string;
      writeback_dataset?: string;
      edit_function?: string;
      sections?: ActionParameterSection[];
    },
  ) => api.put<ActionType>(`${KNOWLEDGE_URL}/action-types/${name}`, body),

  listInterfaces: () => api.get<InterfaceType[]>(`${KNOWLEDGE_URL}/interfaces`),
  createInterface: (body: { name: string; required_properties?: string[]; required_actions?: string[]; description?: string }) =>
    api.post<InterfaceType>(`${KNOWLEDGE_URL}/interfaces`, body),
  updateInterface: (
    name: string,
    body: { required_properties?: string[]; required_actions?: string[]; description?: string },
  ) => api.put<InterfaceType>(`${KNOWLEDGE_URL}/interfaces/${name}`, body),

  listMarkings: () => api.get<Marking[]>(`${KNOWLEDGE_URL}/markings`),
  createMarking: (body: { name: string; description?: string }) => api.post<Marking>(`${KNOWLEDGE_URL}/markings`, body),
};
