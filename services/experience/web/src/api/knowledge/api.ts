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
  ActionTypeObservability,
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
  ObjectSet,
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
  CatalogDataset,
  HealthCheckFinding,
  ActionApproval,
  ActionApprovalStatus,
  TypeClassCatalogEntry,
  ObjectListPage,
} from "./types";

export const knowledgeApi = {
  listObjectTypes: () => api.get<ObjectType[]>(`${KNOWLEDGE_URL}/ontology`),
  getOntologyHealthCheck: () => api.get<HealthCheckFinding[]>(`${KNOWLEDGE_URL}/ontology/health-check`),
  getObjectType: (name: string) => api.get<ObjectType>(`${KNOWLEDGE_URL}/ontology/${name}`),
  listDatasets: () => api.get<CatalogDataset[]>(`${KNOWLEDGE_URL}/catalog/datasets`),
  previewDataset: (datasetName: string) =>
    api.get<{ columns: DatasetPreviewColumn[] }>(`${KNOWLEDGE_URL}/catalog/datasets/${datasetName}/preview`),
  createObjectType: (body: {
    name: string;
    source_dataset_urn: string;
    property_mapping: Record<string, string>;
    description?: string;
    column_classification?: Record<string, string>;
    primary_key?: string;
    title_key?: string | null;
    plural_display_name?: string;
    lifecycle_status?: string;
    visibility?: string;
    icon?: string | null;
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
      link_constraint_bindings?: Record<string, Record<string, string>>;
      interface_property_bindings?: Record<string, Record<string, string>>;
      primary_key?: string;
      title_key?: string | null;
      plural_display_name?: string;
      lifecycle_status?: string;
      visibility?: string;
      icon?: string | null;
      deprecation_reason?: string | null;
      deprecation_deadline?: string | null;
      replacement_urn?: string | null;
    },
  ) => api.post<ObjectTypeVersion>(`${KNOWLEDGE_URL}/ontology/${name}/versions`, body),
  publishObjectTypeVersion: (name: string, version: number) =>
    api.post<ObjectType>(`${KNOWLEDGE_URL}/ontology/${name}/versions/${version}/publish`),
  reindexObjectTypeSearch: (name: string) =>
    api.post<{ object_type: string; indexed: number; skipped_invalid: number; materialized_total: number }>(
      `${KNOWLEDGE_URL}/ontology/${name}/reindex-search`,
    ),

  listObjectsPage: (
    objectType: string,
    opts?: { pageSize?: number; cursor?: string | null },
  ) => {
    const params = new URLSearchParams();
    params.set("page_size", String(opts?.pageSize ?? 50));
    if (opts?.cursor) params.set("cursor", opts.cursor);
    return api.get<ObjectListPage>(`${KNOWLEDGE_URL}/objects/${objectType}?${params}`);
  },
  /** Follows cursors up to Knowledge MAX_WALK_ITEMS for callers that need a full set. */
  listObjects: async (objectType: string) => {
    const items: Array<Record<string, unknown>> = [];
    let cursor: string | null | undefined = null;
    let seen = 0;
    const walkMax = 10_000;
    do {
      const params = new URLSearchParams();
      params.set("page_size", "100");
      if (cursor) params.set("cursor", cursor);
      const page = await api.get<ObjectListPage>(`${KNOWLEDGE_URL}/objects/${objectType}?${params}`);
      items.push(...page.items);
      cursor = page.next_cursor;
      seen += page.items.length;
      if (seen >= walkMax) break;
    } while (cursor);
    return items;
  },
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

  /** Bounded sequential batch (Knowledge cap: 50 instance_ids). */
  invokeActionBatch: (
    objectType: string,
    actionName: string,
    body: {
      reason: string;
      instance_ids: string[];
      parameters?: Record<string, unknown>;
    },
  ) =>
    api.post<{
      action: string;
      objectType: string;
      count: number;
      succeeded: number;
      failed: number;
      results: Array<{ instanceId: string; ok: boolean; result?: Record<string, unknown>; error?: string }>;
    }>(`${KNOWLEDGE_URL}/objects/${objectType}/actions/${actionName}/batch`, {
      reason: body.reason,
      instance_ids: body.instance_ids,
      parameters: body.parameters ?? {},
    }),

  revertActionInvocation: (invocationId: number) =>
    api.post<Record<string, unknown>>(`${KNOWLEDGE_URL}/action-invocations/${invocationId}/revert`),

  getLineage: (urn: string) => api.get<LineageEdge[]>(`${KNOWLEDGE_URL}/lineage/${encodeURIComponent(urn)}`),

  getObjectGraph: (objectType: string, id: string | number, hops = 2) =>
    api.get<InstanceGraph>(`${KNOWLEDGE_URL}/objects/${objectType}/${encodeURIComponent(String(id))}/graph?hops=${hops}`),

  getObjectTimeline: (objectType: string, id: string | number) =>
    api.get<TimelineEvent[]>(`${KNOWLEDGE_URL}/objects/${objectType}/${encodeURIComponent(String(id))}/timeline`),

  search: (
    q: string,
    options?: {
      objectType?: string;
      interface?: string;
      from?: number;
      size?: number;
      propFilters?: Record<string, string>;
    },
  ) => {
    const params = new URLSearchParams({ q });
    if (options?.objectType) params.set("object_type", options.objectType);
    if (options?.interface) params.set("interface", options.interface);
    if (options?.from !== undefined) params.set("from", String(options.from));
    if (options?.size) params.set("size", String(options.size));
    for (const [prop, value] of Object.entries(options?.propFilters ?? {})) {
      if (value) params.set(`prop.${prop}`, value);
    }
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
    source_property?: string;
    target_property: string;
    cardinality: string;
    storage_kind?: string;
    join_dataset_urn?: string;
    join_source_column?: string;
    join_target_column?: string;
    mid_object_type?: string;
    mid_source_property?: string;
    mid_target_property?: string;
    source_display_name?: string;
    source_plural_display_name?: string;
    source_api_name?: string;
    source_visibility?: string;
    target_display_name?: string;
    target_plural_display_name?: string;
    target_api_name?: string;
    target_visibility?: string;
    lifecycle_status?: string;
    type_classes?: string[];
    project_urn?: string;
    deprecation_reason?: string;
    deprecation_deadline?: string;
    replacement_urn?: string;
  }) => api.post<RelationType>(`${KNOWLEDGE_URL}/relation-types`, body),
  updateRelationType: (
    name: string,
    body: {
      target_property?: string;
      cardinality?: string;
      storage_kind?: string;
      join_dataset_urn?: string;
      join_source_column?: string;
      join_target_column?: string;
      mid_object_type?: string;
      mid_source_property?: string;
      mid_target_property?: string;
      source_display_name?: string;
      source_plural_display_name?: string;
      source_api_name?: string;
      source_visibility?: string;
      target_display_name?: string;
      target_plural_display_name?: string;
      target_api_name?: string;
      target_visibility?: string;
      lifecycle_status?: string;
      type_classes?: string[];
      project_urn?: string;
      clear_project_urn?: boolean;
      deprecation_reason?: string;
      deprecation_deadline?: string;
      replacement_urn?: string;
    },
  ) => api.put<RelationType>(`${KNOWLEDGE_URL}/relation-types/${name}`, body),
  deleteRelationType: (name: string) =>
    api.delete<{ name: string; deleted: boolean }>(`${KNOWLEDGE_URL}/relation-types/${name}`),
  getRelationTypePermissions: (name: string) =>
    api.get<{
      urn: string;
      parent_workspace_urn: string;
      project_urn?: string | null;
      permissions: { read: boolean; write: boolean; approve: boolean };
    }>(`${KNOWLEDGE_URL}/relation-types/${encodeURIComponent(name)}/permissions`),

  getObjectLinks: (objectType: string, id: string | number, linkName: string) =>
    api.get<ObjectLinksResponse>(
      `${KNOWLEDGE_URL}/objects/${objectType}/${encodeURIComponent(String(id))}/links/${encodeURIComponent(linkName)}`,
    ),
  putObjectLink: (objectType: string, id: string | number, linkName: string, targetId: unknown) =>
    api.put<ObjectLinksResponse>(
      `${KNOWLEDGE_URL}/objects/${objectType}/${encodeURIComponent(String(id))}/links/${encodeURIComponent(linkName)}`,
      { target_id: targetId },
    ),
  deleteObjectLink: (objectType: string, id: string | number, linkName: string, targetId?: unknown) => {
    const qs =
      targetId === undefined || targetId === null
        ? ""
        : `?target_id=${encodeURIComponent(String(targetId))}`;
    return api.delete<ObjectLinksResponse>(
      `${KNOWLEDGE_URL}/objects/${objectType}/${encodeURIComponent(String(id))}/links/${encodeURIComponent(linkName)}${qs}`,
    );
  },

  generateJoinDataset: (body: { name: string; source_column: string; target_column: string }) =>
    api.post<{
      dataset_urn: string;
      dataset_version_urn: string;
      dataset_name: string;
      source_column: string;
      target_column: string;
      snapshot_id: number;
    }>(`${KNOWLEDGE_URL}/catalog/join-datasets`, body),

  getRelationTypeWritebackStatus: (name: string) =>
    api.get<{
      name: string;
      urn: string;
      storage_kind: string;
      lifecycle_status: string;
      overlay_count: number;
      warnings: string[];
      has_writeback_risk: boolean;
    }>(`${KNOWLEDGE_URL}/relation-types/${encodeURIComponent(name)}/writeback-status`),

  listObjectTypeGroups: () => api.get<ObjectTypeGroup[]>(`${KNOWLEDGE_URL}/object-type-groups`),
  createObjectTypeGroup: (body: { name: string; description?: string; object_types: string[] }) =>
    api.post<ObjectTypeGroup>(`${KNOWLEDGE_URL}/object-type-groups`, body),
  updateObjectTypeGroup: (
    name: string,
    body: { name: string; description?: string; object_types: string[] },
  ) => api.put<ObjectTypeGroup>(`${KNOWLEDGE_URL}/object-type-groups/${encodeURIComponent(name)}`, body),
  deleteObjectTypeGroup: (name: string) =>
    api.delete<void>(`${KNOWLEDGE_URL}/object-type-groups/${encodeURIComponent(name)}`),

  listObjectSets: () => api.get<ObjectSet[]>(`${KNOWLEDGE_URL}/object-sets`),
  createObjectSet: (body: {
    name: string;
    object_type: string;
    definition: object;
    display_name?: string;
    description?: string;
    lifecycle_status?: string;
    visibility?: string;
  }) => api.post<ObjectSet>(`${KNOWLEDGE_URL}/object-sets`, body),
  updateObjectSet: (
    name: string,
    body: {
      definition?: object;
      display_name?: string;
      description?: string;
      lifecycle_status?: string;
      visibility?: string;
    },
  ) => api.put<ObjectSet>(`${KNOWLEDGE_URL}/object-sets/${name}`, body),
  evaluateObjectSet: (name: string) =>
    api.get<{ object_set: string; object_type: string; count: number; items: Array<Record<string, unknown>> }>(
      `${KNOWLEDGE_URL}/object-sets/${name}/objects`,
    ),

  listValueTypes: () => api.get<ValueType[]>(`${KNOWLEDGE_URL}/value-types`),
  createValueType: (body: {
    name: string;
    base_type: string;
    format_regex?: string;
    format_regex_match?: string;
    constraints?: ValueTypeConstraint[];
    description?: string;
    api_name?: string;
    display_name?: string;
    example_value?: string;
    lifecycle_status?: string;
    project_urn?: string;
    deprecation_reason?: string;
    deprecation_deadline?: string;
    replacement_urn?: string;
  }) => api.post<ValueType>(`${KNOWLEDGE_URL}/value-types`, body),
  updateValueType: (
    name: string,
    body: {
      format_regex?: string | null;
      format_regex_match?: string;
      constraints?: ValueTypeConstraint[];
      description?: string;
      api_name?: string;
      display_name?: string;
      example_value?: string | null;
      clear_example_value?: boolean;
      lifecycle_status?: string;
      project_urn?: string;
      clear_project_urn?: boolean;
      deprecation_reason?: string;
      deprecation_deadline?: string;
      replacement_urn?: string;
    },
  ) => api.put<ValueType>(`${KNOWLEDGE_URL}/value-types/${name}`, body),
  listValueTypeRevisions: (name: string) =>
    api.get<ValueType[]>(`${KNOWLEDGE_URL}/value-types/${encodeURIComponent(name)}/revisions`),
  deprecateValueType: (
    name: string,
    body: { deprecation_reason: string; deprecation_deadline: string; replacement_urn?: string },
  ) => api.post<ValueType>(`${KNOWLEDGE_URL}/value-types/${encodeURIComponent(name)}/deprecate`, body),
  getValueTypePermissions: (name: string) =>
    api.get<{
      name: string;
      urn: string;
      parent_workspace_urn: string;
      project_urn: string | null;
      permissions: { read: boolean; write: boolean; approve: boolean };
    }>(`${KNOWLEDGE_URL}/value-types/${encodeURIComponent(name)}/permissions`),

  listSharedPropertyTypes: () => api.get<SharedPropertyType[]>(`${KNOWLEDGE_URL}/shared-property-types`),
  createSharedPropertyType: (body: {
    api_name: string;
    display_name: string;
    value_type?: string;
    struct_properties?: Record<
      string,
      { kind: string; value_type?: string; shared_property_type?: string; description?: string; main_field?: boolean }
    >;
    description?: string;
    visibility?: "prominent" | "normal" | "hidden";
    render_hints?: string[];
    type_classes?: string[];
    property_format?: PropertyFormatRule | null;
    aliases?: string[];
    project_urn?: string | null;
  }) => api.post<SharedPropertyType>(`${KNOWLEDGE_URL}/shared-property-types`, body),
  updateSharedPropertyType: (
    apiName: string,
    body: {
      display_name?: string;
      description?: string;
      visibility?: "prominent" | "normal" | "hidden";
      render_hints?: string[];
      type_classes?: string[];
      property_format?: PropertyFormatRule | null;
      clear_property_format?: boolean;
      aliases?: string[];
      project_urn?: string | null;
      clear_project_urn?: boolean;
    },
  ) => api.put<SharedPropertyType>(`${KNOWLEDGE_URL}/shared-property-types/${apiName}`, body),
  getSharedPropertyTypeUsage: (apiName: string) =>
    api.get<{ object_type: string }[]>(`${KNOWLEDGE_URL}/shared-property-types/${apiName}/usage`),
  getSharedPropertyTypePermissions: (apiName: string) =>
    api.get<{
      api_name: string;
      urn: string;
      parent_workspace_urn: string;
      project_urn?: string | null;
      permissions: { read: boolean; write: boolean; approve: boolean };
    }>(`${KNOWLEDGE_URL}/shared-property-types/${apiName}/permissions`),
  deleteSharedPropertyType: (apiName: string) =>
    api.delete<{ api_name: string; urn: string; detached_object_types: string[] }>(
      `${KNOWLEDGE_URL}/shared-property-types/${apiName}`,
    ),

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
    notify_webhook?: string;
    edit_function?: string;
    sections?: ActionParameterSection[];
    type_classes?: string[];
    lifecycle_status?: string;
    deprecation_reason?: string;
    deprecation_deadline?: string;
    replacement_urn?: string;
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
      notify_webhook?: string;
      edit_function?: string;
      sections?: ActionParameterSection[];
      type_classes?: string[];
      lifecycle_status?: string;
      deprecation_reason?: string;
      deprecation_deadline?: string;
      replacement_urn?: string;
    },
  ) => api.put<ActionType>(`${KNOWLEDGE_URL}/action-types/${encodeURIComponent(name)}`, body),
  getActionTypeObservability: (name: string, days = 30) =>
    api.get<ActionTypeObservability>(
      `${KNOWLEDGE_URL}/action-types/${encodeURIComponent(name)}/observability?days=${days}`,
    ),

  listInterfaces: () => api.get<InterfaceType[]>(`${KNOWLEDGE_URL}/interfaces`),
  listInterfaceObjects: async (name: string) => {
    const items: Array<Record<string, unknown>> = [];
    let cursor: string | null | undefined = null;
    let seen = 0;
    do {
      const params = new URLSearchParams();
      params.set("page_size", "100");
      if (cursor) params.set("cursor", cursor);
      const page = await api.get<ObjectListPage>(
        `${KNOWLEDGE_URL}/interfaces/${encodeURIComponent(name)}/objects?${params}`,
      );
      items.push(...page.items);
      cursor = page.next_cursor;
      seen += page.items.length;
      if (seen >= 10_000) break;
    } while (cursor);
    return items;
  },
  createInterface: (body: {
    name: string;
    required_properties?: string[];
    required_actions?: string[];
    description?: string;
    lifecycle_status?: string;
    deprecation_reason?: string;
    deprecation_deadline?: string;
    replacement_urn?: string;
    property_types?: InterfaceType["property_types"];
    link_constraints?: InterfaceType["link_constraints"];
    parent_interfaces?: string[];
  }) => api.post<InterfaceType>(`${KNOWLEDGE_URL}/interfaces`, body),
  updateInterface: (
    name: string,
    body: {
      required_properties?: string[];
      required_actions?: string[];
      description?: string;
      lifecycle_status?: string;
      deprecation_reason?: string;
      deprecation_deadline?: string;
      replacement_urn?: string;
      property_types?: InterfaceType["property_types"];
      link_constraints?: InterfaceType["link_constraints"];
      parent_interfaces?: string[];
    },
  ) => api.put<InterfaceType>(`${KNOWLEDGE_URL}/interfaces/${name}`, body),
  deleteInterface: (name: string) =>
    api.delete<InterfaceType>(`${KNOWLEDGE_URL}/interfaces/${encodeURIComponent(name)}`),

  listMarkings: () => api.get<Marking[]>(`${KNOWLEDGE_URL}/markings`),
  createMarking: (body: { name: string; description?: string }) => api.post<Marking>(`${KNOWLEDGE_URL}/markings`, body),

  listTypeClasses: () => api.get<TypeClassCatalogEntry[]>(`${KNOWLEDGE_URL}/type-classes`),

  listApprovals: (status?: ActionApprovalStatus) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    const qs = params.toString();
    return api.get<ActionApproval[]>(`${KNOWLEDGE_URL}/approvals${qs ? `?${qs}` : ""}`);
  },
  getApproval: (id: number) => api.get<ActionApproval>(`${KNOWLEDGE_URL}/approvals/${id}`),
  approveApproval: (id: number, note?: string) =>
    api.post<{ status: string; approvalId: number }>(`${KNOWLEDGE_URL}/approvals/${id}/approve`, { note }),
  rejectApproval: (id: number, note?: string) =>
    api.post<{ status: string; approvalId: number }>(`${KNOWLEDGE_URL}/approvals/${id}/reject`, { note }),
};
