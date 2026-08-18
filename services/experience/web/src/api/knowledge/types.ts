// `numeric`'s fields are named to match JS `Intl.NumberFormat` options
// exactly (see `PropertyFormat.tsx`) — same names `knowledge`'s
// `_validate_property_formats` validates server-side, no translation
// layer needed on either side.
export type PropertyFormatRule =
  | { kind: "currency"; currency: string }
  | { kind: "badge"; colors: Record<string, string> }
  | {
      kind: "numeric";
      style?: "decimal" | "currency" | "percent" | "unit";
      currency?: string;
      unit?: string;
      prefix?: string;
      suffix?: string;
      useGrouping?: boolean;
      notation?: "standard" | "compact" | "scientific" | "engineering";
      minimumFractionDigits?: number;
      maximumFractionDigits?: number;
      minimumSignificantDigits?: number;
      maximumSignificantDigits?: number;
      minimumIntegerDigits?: number;
    }
  | { kind: "datetime"; style: "date" | "datetime-long" | "datetime-short" | "iso8601" | "relative" | "time"; timezone?: string }
  | { kind: "principal" }
  | { kind: "resource-link"; resourceType: "object-type" | "application" };

// Conditional formatting — a sibling concept to PropertyFormatRule above,
// not a `style` folded into it: this styles a value already rendered by
// FormattedValue, rather than controlling the value's own textual form.
export type ConditionalFormatCondition =
  | { type: "always" }
  | { type: "is-null" }
  | { type: "string-equals" | "string-contains" | "string-starts-with"; value: string; caseSensitive?: boolean }
  | { type: "number-range"; min?: number; max?: number }
  | { type: "number-equals"; value: number };

export interface ConditionalFormatStyle {
  color?: string;
  backgroundColor?: string;
  textAlign?: "left" | "center" | "right";
}

export interface ConditionalFormatRule {
  condition: ConditionalFormatCondition;
  compareTo?: { kind: "property"; property: string };
  style: ConditionalFormatStyle;
}

// A leaf/struct/array declaration — one level of struct/array nesting
// only, the same limit `ontology/publishing.py`'s `_validate_property_types`
// enforces server-side (a struct's own properties, or an array's
// element, may only ever be a `value_type`/`shared_property_type` leaf).
// Optional `description` / `main_field` are Foundry-style field metadata
// (compact Explorer display uses main fields when any are marked).
type PropertyTypeLeaf =
  | {
      kind: "value_type";
      value_type: string;
      description?: string;
      main_field?: boolean;
      /** Optional dataset column for this field (Foundry Column mapping). */
      column?: string;
    }
  | {
      kind: "shared_property_type";
      shared_property_type: string;
      description?: string;
      main_field?: boolean;
      column?: string;
    };

// `editable`/`required`/`visibility`/`render_hints`/`type_classes` (property
// control) only ever apply at this top level — never inside a nested
// `struct.properties`/`array.element` entry, which stays a plain
// `PropertyTypeLeaf`.
// `kind` may be omitted for metadata-only entries (visibility etc.).
export type PropertyRenderHint =
  | "searchable"
  | "sortable"
  | "selectable"
  | "identifier"
  | "keywords"
  | "long_text"
  | "low_cardinality"
  | "enable_leading_wildcards"
  | "enable_regex_queries";

export type RegistryLifecycleStatus = "experimental" | "active" | "deprecated" | "example";
export type ObjectTypeLifecycleStatus = RegistryLifecycleStatus | "promoted";
export type PropertyLifecycleStatus = RegistryLifecycleStatus;

export type PropertyTypeRule = {
  editable?: boolean;
  required?: boolean;
  visibility?: "prominent" | "normal" | "hidden";
  /** Foundry-style render hints. Absent ⇒ searchable by default at index time. */
  render_hints?: PropertyRenderHint[];
  /** Free-form type classes (bare tags or Foundry kind:name). */
  type_classes?: string[];
  lifecycle_status?: PropertyLifecycleStatus;
} & (
  | { kind?: undefined }
  | PropertyTypeLeaf
  | { kind: "struct"; properties: Record<string, PropertyTypeLeaf> }
  | {
      kind: "array";
      element: PropertyTypeLeaf | { kind: "struct"; properties: Record<string, PropertyTypeLeaf> };
    }
);

// A derived property is either a Function plugin name (string) or a
// Foundry-style reducer over a RelationType — `path` (1–3 hops) matches
// the same forward-local-name-or-target_property convention the `/links`
// endpoint resolves server-side; `property` names a property on the
// *final* related ObjectType and is required unless `aggregate` is "count".
export interface DerivedPropertyLinkAggregate {
  kind: "link_aggregate";
  /** Link accessor names, 1–3 hops (Foundry multi-hop derived properties). */
  path: string[];
  aggregate: "sum" | "count" | "avg" | "min" | "max" | "collect_list" | "collect_set";
  property?: string;
  /** Cap for collect_list / collect_set (default 10). */
  collect_limit?: number;
}

// A reducer over one of *this* ObjectType's own array properties
// (struct array or scalar array) — `property` must be an `array`-kind
// `property_types` entry; `by` names the struct field to compare for
// latest/earliest/max/min, and must be absent for a scalar array
// (the raw values are compared directly).
export interface DerivedPropertyStructReducer {
  kind: "struct_reducer";
  property: string;
  reducer: "first" | "last" | "latest" | "earliest" | "max" | "min";
  by?: string;
}

export type DerivedPropertyValue = string | DerivedPropertyLinkAggregate | DerivedPropertyStructReducer;

export interface ObjectType {
  urn: string;
  tenant_id: string;
  name: string;
  source_dataset_urn: string;
  property_mapping: Record<string, string>;
  property_formats: Record<string, PropertyFormatRule>;
  conditional_formats?: Record<string, ConditionalFormatRule[]>;
  property_types?: Record<string, PropertyTypeRule>;
  implements?: string[];
  derived_properties?: Record<string, DerivedPropertyValue>;
  markings?: string[];
  /** Per-interface map of constraint api_name → RelationType name. */
  link_constraint_bindings?: Record<string, Record<string, string>>;
  /** Map interface required props → OT property or struct field path (`address.city`). */
  interface_property_bindings?: Record<string, Record<string, string>>;
  classification: "public" | "internal" | "confidential" | "restricted";
  description: string;
  version: number;
  created_at: string;
  column_classification?: Record<string, string>;
  project_urn?: string | null;
  primary_key?: string;
  title_key?: string | null;
  plural_display_name?: string;
  lifecycle_status?: "experimental" | "active" | "deprecated";
  visibility?: "prominent" | "normal" | "hidden";
  icon?: string | null;
  deprecation_reason?: string | null;
  deprecation_deadline?: string | null;
  replacement_urn?: string | null;
}

export type ValueTypeBaseType =
  | "string"
  | "integer"
  | "double"
  | "boolean"
  | "date"
  | "timestamp"
  | "short"
  | "byte"
  | "long"
  | "decimal"
  | "float"
  | "geopoint"
  | "geoshape"
  | "vector";

export type ValueTypeConstraint =
  | { kind: "enum"; values: (string | number)[]; caseSensitive?: boolean }
  | { kind: "range"; min?: number; max?: number }
  | { kind: "rid" }
  | { kind: "uuid" };

export interface ValueType {
  tenant_id: string;
  name: string;
  base_type: ValueTypeBaseType;
  format_regex: string | null;
  format_regex_match?: "full" | "substring";
  constraints: ValueTypeConstraint[];
  description: string;
  api_name?: string;
  display_name?: string;
  example_value?: string | null;
  version?: number;
  lifecycle_status?: "experimental" | "active" | "deprecated";
  deprecation_reason?: string | null;
  deprecation_deadline?: string | null;
  replacement_urn?: string | null;
  project_urn?: string | null;
  urn?: string;
  created_at: string;
}

export interface SharedPropertyType {
  tenant_id: string;
  api_name: string;
  display_name: string;
  /** Holon RID equivalent: hl:{tenant}:global:shared-property-type:{api_name} */
  urn?: string;
  /** Null when this SPT is struct-typed (`struct_properties` set). */
  value_type: string | null;
  /** One-level struct field map when this SPT is struct-typed. */
  struct_properties?: Record<string, PropertyTypeLeaf> | null;
  description: string;
  /** Foundry aliases — alternate search terms. */
  aliases?: string[];
  /** Optional project scope (additive ReBAC via parent_project). */
  project_urn?: string | null;
  visibility?: "prominent" | "normal" | "hidden";
  render_hints?: PropertyRenderHint[];
  type_classes?: string[];
  property_format?: PropertyFormatRule | null;
  created_at: string;
}

export interface ActionParameterDefault {
  kind: "static" | "current_object" | "object_property";
  /** Required when kind=static. */
  value?: unknown;
  /**
   * object_property only: `"current"` (Action target) or an earlier
   * object_reference parameter name (Foundry order rule).
   */
  object?: string;
  /** object_property only — property key on the source object. */
  property?: string;
}

export interface ActionParameter {
  name: string;
  required: boolean;
  // Omitted (undefined) means "value_type", the original/default shape —
  // same omittable-discriminator convention `property_types`/
  // `derived_properties` already use.
  kind?: "value_type" | "object_reference";
  value_type?: string;
  object_type?: string;
  /** Optional Object Set name — filters the object_reference Suggest + invoke check. */
  object_set?: string;
  /** Foundry type classes on the parameter, e.g. `actions:generate_uuid`. */
  type_classes?: string[];
  /** Foundry Form default — prefills the invoke form (OE / Object App / …). */
  default?: ActionParameterDefault;
}

export interface ActionEdit {
  /** Defaults to modify_property when omitted (backward compatible). */
  kind?: "modify_property" | "create_link" | "delete_link" | "create_object" | "delete_object";
  // modify_property
  property?: string;
  source?: "parameter" | "literal";
  parameter_name?: string;
  value?: unknown;
  // create_link / delete_link
  relation_type?: string;
  source_from?: "target_instance" | "parameter" | "literal";
  source_parameter?: string;
  source_value?: string;
  target_from?: "target_instance" | "parameter" | "literal";
  target_parameter?: string;
  target_value?: string;
  // create_object
  object_type?: string;
  primary_key?: {
    source?: "parameter" | "generate_uuid" | "literal";
    parameter_name?: string;
    value?: unknown;
  };
  properties?: Array<{
    property: string;
    source: "parameter" | "literal";
    parameter_name?: string;
    value?: unknown;
  }>;
  // delete_object
  // target_from + parameter_name / object_type reused above
}

export interface SubmissionCriterion {
  property?: string;
  operator?: "eq" | "neq" | "gt" | "gte" | "lt" | "lte" | "in";
  value?: unknown;
  message?: string;
  principal?: "urn" | "type";
  all?: SubmissionCriterion[];
  any?: SubmissionCriterion[];
}

// Configure/Sections: a purely-display grouping of an Action Type's
// parameters in the invocation form (Foundry's "Sections") — never
// affects what gets submitted. A parameter not named in any section
// renders ungrouped, same as before this existed.
export interface ActionParameterSection {
  name: string;
  parameter_names: string[];
}

export interface ActionType {
  tenant_id: string;
  name: string;
  // Exactly one of these two is ever set — an Action Type targets either
  // one ObjectType or one Interface (Actions on interfaces), never both.
  target_object_type: string | null;
  target_interface?: string | null;
  required_permission: string;
  risk_level: "low" | "high";
  description: string;
  parameters: ActionParameter[];
  edits: ActionEdit[];
  submission_criteria: SubmissionCriterion[];
  function_side_effect: string | null;
  writeback_dataset: string | null;
  notify_webhook?: string | null;
  // Function-backed Actions: mutually exclusive with a non-empty `edits`
  // — the named Function plugin's return value becomes the applied
  // edits, instead of a fixed declaration.
  edit_function?: string | null;
  sections?: ActionParameterSection[];
  /** Foundry type classes, e.g. `hubble-oe:hide-action`. */
  type_classes?: string[];
  lifecycle_status?: "experimental" | "active" | "deprecated";
  deprecation_reason?: string | null;
  deprecation_deadline?: string | null;
  replacement_urn?: string | null;
  created_at: string;
}

/** Ontology Manager Action Observability (last N days). */
export interface ActionTypeObservability {
  action_name: string;
  days: number;
  invocations: number;
  reverted: number;
  with_edits: number;
  approvals: {
    pending: number;
    approved: number;
    rejected: number;
    expired: number;
  };
  by_day: Array<{ day: string; invocations: number }>;
}

export interface InterfaceLinkConstraint {
  api_name: string;
  target_kind: "object_type" | "interface";
  target: string;
  cardinality: "one" | "many";
  required: boolean;
  description?: string;
}

export interface InterfaceType {
  tenant_id: string;
  name: string;
  required_properties: string[];
  required_actions: string[];
  /** Optional typed bindings for required_properties (value_type / shared_property_type). */
  property_types?: Record<
    string,
    | { kind: "value_type"; value_type: string }
    | { kind: "shared_property_type"; shared_property_type: string }
  >;
  /** Abstract link constraints fulfilled by RelationTypes on implementers. */
  link_constraints?: InterfaceLinkConstraint[];
  /** Interfaces this one extends (Foundry inheritance). */
  parent_interfaces?: string[];
  description: string;
  lifecycle_status?: "experimental" | "active" | "deprecated";
  deprecation_reason?: string | null;
  deprecation_deadline?: string | null;
  replacement_urn?: string | null;
  created_at: string;
}

export interface MarkingCategory {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  category_type: "CONJUNCTIVE" | "DISJUNCTIVE";
  marking_type: "MANDATORY";
  created_at: string;
}

export interface Marking {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  category_id: string;
  category_name?: string;
  category_type?: "CONJUNCTIVE" | "DISJUNCTIVE";
  marking_type?: "MANDATORY";
  created_at: string;
}

export interface TypeClassCatalogEntry {
  id: string;
  kind: string;
  name: string;
  applies_to: string;
  description: string;
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
  implements: string[];
  derived_properties: Record<string, DerivedPropertyValue>;
  project_urn: string | null;
  markings: string[];
  property_formats: Record<string, PropertyFormatRule>;
  conditional_formats: Record<string, ConditionalFormatRule[]>;
  property_types: Record<string, PropertyTypeRule>;
  link_constraint_bindings?: Record<string, Record<string, string>>;
  /** Map interface required props → OT property or struct field path (`address.city`). */
  interface_property_bindings?: Record<string, Record<string, string>>;
  primary_key?: string;
  title_key?: string | null;
  plural_display_name?: string;
  lifecycle_status?: "experimental" | "active" | "deprecated";
  visibility?: "prominent" | "normal" | "hidden";
  icon?: string | null;
}

// The `ontology_branch` table backs both the ObjectType-specific branch
// flow (`object_type_urn`/`version` set, `resource_type` defaults to
// 'object_type', `resource_name`/`proposed_definition` null) and the
// generic 4-registry flow (`resource_type`/`resource_name`/
// `proposed_definition` set, `object_type_urn`/`version` null) — see
// `ontology/resource_branching.py`'s module docstring. One flat type
// mirrors the raw row rather than forcing a discriminated union neither
// backend shape actually needs.
export interface Branch {
  id: number;
  tenant_id: string;
  branch_name: string;
  status: "open" | "merged";
  created_by_urn: string;
  created_at: string;
  object_type_urn: string | null;
  version: number | null;
  resource_type: string | null;
  resource_name: string | null;
  proposed_definition: string | null;
}

export interface BranchReview {
  id: number;
  branch_id: number;
  tenant_id: string;
  reviewer_urn: string;
  decision: "approved" | "changes_requested";
  note: string | null;
  decided_at: string;
}

export type ResourceBranchKind = "interface_type" | "relation_type" | "value_type" | "shared_property_type" | "action_type";

// Internal — used only within api.ts to type branch creation/update bodies.
export interface ObjectTypeBranchFields {
  property_mapping?: Record<string, string>;
  description?: string;
  implements?: string[];
  derived_properties?: Record<string, DerivedPropertyValue>;
  project_urn?: string;
  markings?: string[];
  property_formats?: Record<string, PropertyFormatRule>;
  conditional_formats?: Record<string, ConditionalFormatRule[]>;
  property_types?: Record<string, PropertyTypeRule>;
}

export interface ActionDefinition {
  name: string;
  target_object_type: string | null;
  target_interface?: string | null;
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
  edits?: ActionEdit[];
  // Function-backed Actions: mutually exclusive with a non-empty `edits`
  // — the named Function plugin's return value becomes the applied
  // edits, instead of a fixed declaration.
  edit_function?: string | null;
  sections?: ActionParameterSection[];
  /** Foundry type classes, e.g. `hubble-oe:hide-action`. */
  type_classes?: string[];
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
  /** Selectable-property histograms when an ObjectType facet is selected. */
  property_facets?: Record<string, Record<string, number>>;
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
  target_property: string;
  cardinality: string;
  storage_kind?: "foreign_key" | "join_dataset" | "object_backed";
  join_dataset_urn?: string | null;
  join_source_column?: string | null;
  join_target_column?: string | null;
  mid_object_type_urn?: string | null;
  mid_source_property?: string | null;
  mid_target_property?: string | null;
  source_display_name?: string;
  source_plural_display_name?: string;
  source_api_name?: string;
  source_visibility?: "prominent" | "normal" | "hidden";
  target_display_name?: string;
  target_plural_display_name?: string;
  target_api_name?: string;
  target_visibility?: "prominent" | "normal" | "hidden";
  lifecycle_status?: "experimental" | "active" | "deprecated";
  deprecation_reason?: string | null;
  deprecation_deadline?: string | null;
  replacement_urn?: string | null;
  type_classes?: string[];
  project_urn?: string | null;
}

export interface ObjectSet {
  urn: string;
  tenant_id: string;
  workspace_id: string;
  name: string;
  display_name: string;
  description: string;
  object_type_urn: string;
  definition: { all: Array<{ property: string; op: string; value: unknown }> };
  lifecycle_status: "experimental" | "active" | "deprecated";
  visibility: "prominent" | "normal" | "hidden";
  created_at: string;
}

export interface TimelineEvent {
  kind: "invoked" | "requested" | "rejected" | "expired";
  action_name: string;
  actor_urn: string | null;
  reason: string;
  at: string;
  id: number | null;
  has_edits: boolean;
  revertible: boolean;
  reverted: boolean;
}

export interface ObjectListPage {
  data: Array<Record<string, unknown>>;
  nextPageToken: string | null;
  pageSize?: number;
}

export interface ObjectLinksResponse {
  relation: string;
  direction: "toward_one" | "toward_many" | null;
  cardinality: string;
  storage_kind?: string;
  data: Array<Record<string, unknown>>;
  nextPageToken?: string | null;
  pageSize?: number;
  link_objects?: Array<{ object_type: string | null; object: Record<string, unknown> }>;
}

export interface ObjectTypeGroup {
  tenant_id: string;
  name: string;
  description: string;
  object_types: string[];
  created_at: string;
}

export interface DatasetPreviewColumn {
  name: string;
  sample: unknown;
}

/** Latest version summary from GET /catalog/datasets. */
export interface CatalogDataset {
  urn: string;
  display_name: string;
  latest_version_urn: string;
  snapshot_id: number | string;
  row_count: number;
  location: string;
  created_at: string;
}

/** One row from GET /catalog/datasets/{name}/versions — full snapshot history. */
export interface DatasetVersion {
  urn: string;
  dataset_urn: string;
  snapshot_id: number | string;
  row_count: number;
  location: string;
  created_at: string;
}

export interface DatasetStatColumn {
  name: string;
  type: string;
  required: boolean;
  null_count: number | null;
  distinct_count: number | null;
  min: string | null;
  max: string | null;
}

/** GET /catalog/datasets/{name}/stats — real Iceberg schema + per-column stats. */
export interface DatasetStats {
  row_count: number;
  columns: DatasetStatColumn[];
}

export interface HealthCheckFinding {
  kind:
    | "action_sprawl"
    | "god_object"
    | "misnomer_property"
    | "misnomer_type"
    | "dry_duplication"
    | "time_machine"
    | "missing_primary_key"
    | "missing_title_key"
    | "mn_without_join"
    | "join_dataset_incomplete"
    | "object_backed_incomplete"
    | "link_overlays_present"
    | "value_type_violation";
  object_type: string;
  severity: "warning" | "error";
  detail: string;
}

export type ActionApprovalStatus = "pending" | "approved" | "rejected" | "expired" | "failed";

/** Knowledge `action_approval` row — snake_case as returned by GET /approvals. */
export interface ActionApproval {
  id: number;
  tenant_id: string;
  action_name: string;
  instance_urn: string;
  requested_by_urn: string;
  reason: string;
  status: ActionApprovalStatus;
  requested_at: string;
  expires_at: string;
  decided_by_urn: string | null;
  decided_at: string | null;
  decision_note: string | null;
  parameters?: Record<string, unknown>;
}
