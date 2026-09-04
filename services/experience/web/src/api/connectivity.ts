import { api } from "./client";
import { CONNECTIVITY_URL } from "./config";

export interface GenericSource {
  tenant_id: string;
  name: string;
  base_url: string;
  auth_header_name: string | null;
  has_auth_header_value: boolean;
  record_path: string | null;
  next_page_path: string | null;
  connection_name: string | null;
  schedule_interval_minutes: number | null;
  cursor_property: string | null;
  incremental_param: string | null;
  last_cursor_value: string | null;
  status: "active" | "disabled";
  created_by_urn: string;
  created_at: string;
}

export interface RegisterSourceRequest {
  name: string;
  base_url: string;
  auth_header_name?: string;
  auth_header_value?: string;
  record_path?: string;
  next_page_path?: string;
  connection_name?: string;
  schedule_interval_minutes?: number;
  cursor_property?: string;
  incremental_param?: string;
}

export interface ConnectorPluginManifest {
  name: string;
  version: string;
  plugin_type: string;
  entry_point: string;
  dataset_name: string | null;
  connector_local_name: string | null;
  capabilities: Record<string, unknown>;
}

export interface ConnectorPlugin {
  name: string;
  version: string;
  manifest: ConnectorPluginManifest;
  checksum: string;
  status: "active" | "disabled";
  tenant_id: string | null;
  registered_at: string;
  schedule_interval_minutes: number | null;
}

export interface KafkaStreamSource {
  tenant_id: string;
  name: string;
  topic: string;
  key_field: string;
  dataset_name: string;
  batch_interval_seconds: number;
  status: "active" | "disabled";
  created_by_urn: string;
  created_at: string;
}

export interface RegisterKafkaStreamRequest {
  name: string;
  topic: string;
  key_field: string;
  dataset_name: string;
  batch_interval_seconds?: number;
}

export interface GenericConnection {
  tenant_id: string;
  name: string;
  auth_header_name: string;
  has_auth_header_value: boolean;
  has_secret_ref?: boolean;
  created_by_urn: string;
  created_at: string;
}

export interface RegisterConnectionRequest {
  name: string;
  auth_header_name: string;
  // Optional so editing (same `name`, upsert) can omit it to keep the
  // existing secret — same convention `RegisterSourceRequest` already
  // uses. Required in practice for a brand-new connection, enforced by
  // the create form, not by this type.
  auth_header_value?: string;
  secret_ref?: string;
}

export interface SqlConnection {
  tenant_id: string;
  name: string;
  host: string;
  port: number;
  database: string;
  username: string;
  has_password: boolean;
  created_by_urn: string;
  created_at: string;
}

export interface RegisterSqlConnectionRequest {
  name: string;
  host: string;
  port?: number;
  database: string;
  username: string;
  password?: string;
  secret_ref?: string;
}

export interface SqlSource {
  tenant_id: string;
  name: string;
  workspace_id: string;
  connection_name: string;
  table_name: string | null;
  query: string | null;
  schedule_interval_minutes: number | null;
  cursor_property: string | null;
  last_cursor_value: string | null;
  status: "active" | "disabled";
  created_by_urn: string;
  created_at: string;
}

export interface RegisterSqlSourceRequest {
  name: string;
  connection_name: string;
  table_name?: string;
  query?: string;
  schedule_interval_minutes?: number;
  cursor_property?: string;
}

export type ObjectConnectionKind = "s3" | "azure";

export interface ObjectConnection {
  tenant_id: string;
  name: string;
  kind: ObjectConnectionKind;
  endpoint: string;
  region: string;
  access_key_id: string;
  path_style: boolean;
  has_secret_access_key: boolean;
  created_by_urn: string;
  created_at: string;
}

export interface RegisterObjectConnectionRequest {
  name: string;
  access_key_id: string;
  kind?: ObjectConnectionKind;
  // Required for kind='s3'; defaults to the account's public Blob endpoint
  // for kind='azure' when omitted.
  endpoint?: string;
  region?: string;
  path_style?: boolean;
  secret_access_key?: string;
  secret_ref?: string;
}

export interface ObjectSource {
  tenant_id: string;
  name: string;
  workspace_id: string;
  connection_name: string;
  bucket: string;
  object_key: string | null;
  key_prefix: string | null;
  format: "csv" | "ndjson" | "parquet" | string;
  incremental: boolean;
  last_synced_key: string | null;
  schedule_interval_minutes: number | null;
  status: "active" | "disabled";
  created_by_urn: string;
  created_at: string;
}

export interface RegisterObjectSourceRequest {
  name: string;
  connection_name: string;
  bucket: string;
  format: string;
  object_key?: string;
  key_prefix?: string;
  incremental?: boolean;
  schedule_interval_minutes?: number;
}

export interface SyncResult {
  dataset_urn: string;
  dataset_version_urn: string;
  snapshot_id: number;
  row_count: number;
  location: string;
}

export interface SyncRun {
  dataset_urn: string;
  row_count: number;
  finished_at: string;
}

export interface TransformStep {
  step_name: string;
  input_dataset: string;
  function_name: string;
  output_dataset: string;
  /** Column → Value Type name (Foundry logical type cast). */
  value_type_casts?: Record<string, string>;
}

export interface PipelineLastRun {
  status: "succeeded" | "failed" | string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  row_count: number;
}

export interface PipelineDefinition {
  name: string;
  tenant_id: string;
  steps: TransformStep[];
  created_at: string;
  updated_at: string;
  schedule_interval_minutes: number | null;
  last_run: PipelineLastRun | null;
  last_success_at: string | null;
  lag_seconds: number | null;
}

export interface WriteTarget {
  tenant_id: string;
  dataset_name: string;
  table_name: string;
  id_column: string;
  allowed_properties: Record<string, string>;
  created_by_urn: string;
  created_at: string;
}

export interface RegisterWriteTargetRequest {
  dataset_name: string;
  table_name: string;
  id_column: string;
  allowed_properties: Record<string, string>;
}

export interface PipelineStepResult {
  step_name: string;
  dataset_urn: string;
  dataset_version_urn: string;
  snapshot_id: number;
  row_count: number;
  location: string;
}

export interface PipelineRun {
  id: number;
  tenant_id: string;
  pipeline_name: string;
  status: "succeeded" | "failed" | string;
  started_at: string;
  finished_at: string | null;
  step_results: PipelineStepResult[];
  error: string | null;
}

export const connectivityApi = {
  listSources: () => api.get<GenericSource[]>(`${CONNECTIVITY_URL}/sources`),
  registerSource: (body: RegisterSourceRequest) => api.post<GenericSource>(`${CONNECTIVITY_URL}/sources`, body),
  sync: (dataset: string) => api.post<SyncResult>(`${CONNECTIVITY_URL}/sync`, { dataset }),
  disableSource: (name: string) => api.post<GenericSource>(`${CONNECTIVITY_URL}/sources/${name}/disable`),
  enableSource: (name: string) => api.post<GenericSource>(`${CONNECTIVITY_URL}/sources/${name}/enable`),
  deleteSource: (name: string) => api.delete<{ deleted: string }>(`${CONNECTIVITY_URL}/sources/${name}`),
  listSyncs: () => api.get<SyncRun[]>(`${CONNECTIVITY_URL}/syncs`),
  listPlugins: () => api.get<ConnectorPlugin[]>(`${CONNECTIVITY_URL}/plugins`),
  disablePlugin: (name: string) => api.post<ConnectorPlugin>(`${CONNECTIVITY_URL}/plugins/${name}/disable`),
  enablePlugin: (name: string) => api.post<ConnectorPlugin>(`${CONNECTIVITY_URL}/plugins/${name}/enable`),
  setPluginSchedule: (name: string, scheduleIntervalMinutes: number | null) =>
    api.post<ConnectorPlugin>(`${CONNECTIVITY_URL}/plugins/${name}/schedule`, {
      schedule_interval_minutes: scheduleIntervalMinutes,
    }),
  listKafkaStreams: () => api.get<KafkaStreamSource[]>(`${CONNECTIVITY_URL}/kafka-streams`),
  registerKafkaStream: (body: RegisterKafkaStreamRequest) =>
    api.post<KafkaStreamSource>(`${CONNECTIVITY_URL}/kafka-streams`, body),
  disableKafkaStream: (name: string) => api.post<KafkaStreamSource>(`${CONNECTIVITY_URL}/kafka-streams/${name}/disable`),
  enableKafkaStream: (name: string) => api.post<KafkaStreamSource>(`${CONNECTIVITY_URL}/kafka-streams/${name}/enable`),
  deleteKafkaStream: (name: string) => api.delete<{ deleted: string }>(`${CONNECTIVITY_URL}/kafka-streams/${name}`),

  listConnections: () => api.get<GenericConnection[]>(`${CONNECTIVITY_URL}/connections`),
  registerConnection: (body: RegisterConnectionRequest) => api.post<GenericConnection>(`${CONNECTIVITY_URL}/connections`, body),
  deleteConnection: (name: string) => api.delete<{ deleted: string }>(`${CONNECTIVITY_URL}/connections/${name}`),

  listSqlConnections: () => api.get<SqlConnection[]>(`${CONNECTIVITY_URL}/sql-connections`),
  registerSqlConnection: (body: RegisterSqlConnectionRequest) =>
    api.post<SqlConnection>(`${CONNECTIVITY_URL}/sql-connections`, body),
  deleteSqlConnection: (name: string) => api.delete<{ deleted: string }>(`${CONNECTIVITY_URL}/sql-connections/${name}`),

  listSqlSources: () => api.get<SqlSource[]>(`${CONNECTIVITY_URL}/sql-sources`),
  registerSqlSource: (body: RegisterSqlSourceRequest) =>
    api.post<SqlSource>(`${CONNECTIVITY_URL}/sql-sources`, body),
  disableSqlSource: (name: string) => api.post<SqlSource>(`${CONNECTIVITY_URL}/sql-sources/${name}/disable`),
  enableSqlSource: (name: string) => api.post<SqlSource>(`${CONNECTIVITY_URL}/sql-sources/${name}/enable`),
  deleteSqlSource: (name: string) => api.delete<{ deleted: string }>(`${CONNECTIVITY_URL}/sql-sources/${name}`),

  listObjectConnections: () => api.get<ObjectConnection[]>(`${CONNECTIVITY_URL}/object-connections`),
  registerObjectConnection: (body: RegisterObjectConnectionRequest) =>
    api.post<ObjectConnection>(`${CONNECTIVITY_URL}/object-connections`, body),
  deleteObjectConnection: (name: string) => api.delete<{ deleted: string }>(`${CONNECTIVITY_URL}/object-connections/${name}`),

  listObjectSources: () => api.get<ObjectSource[]>(`${CONNECTIVITY_URL}/object-sources`),
  registerObjectSource: (body: RegisterObjectSourceRequest) =>
    api.post<ObjectSource>(`${CONNECTIVITY_URL}/object-sources`, body),
  disableObjectSource: (name: string) => api.post<ObjectSource>(`${CONNECTIVITY_URL}/object-sources/${name}/disable`),
  enableObjectSource: (name: string) => api.post<ObjectSource>(`${CONNECTIVITY_URL}/object-sources/${name}/enable`),
  deleteObjectSource: (name: string) => api.delete<{ deleted: string }>(`${CONNECTIVITY_URL}/object-sources/${name}`),

  listWriteTargets: () => api.get<WriteTarget[]>(`${CONNECTIVITY_URL}/write-targets`),
  registerWriteTarget: (body: RegisterWriteTargetRequest) =>
    api.post<WriteTarget>(`${CONNECTIVITY_URL}/write-targets`, body),
  deleteWriteTarget: (datasetName: string) =>
    api.delete<{ deleted: string }>(`${CONNECTIVITY_URL}/write-targets/${datasetName}`),

  listPipelines: () => api.get<PipelineDefinition[]>(`${CONNECTIVITY_URL}/pipelines`),
  getPipeline: (name: string) => api.get<PipelineDefinition>(`${CONNECTIVITY_URL}/pipelines/${name}`),
  createPipeline: (name: string, body: { steps: TransformStep[] }) =>
    api.post<PipelineDefinition>(`${CONNECTIVITY_URL}/pipelines/${name}`, body),
  deletePipeline: (name: string) => api.delete<{ deleted: string }>(`${CONNECTIVITY_URL}/pipelines/${name}`),
  runPipeline: (name: string) => api.post<PipelineRun>(`${CONNECTIVITY_URL}/pipelines/${name}/run`),
  listPipelineRuns: (name: string) => api.get<PipelineRun[]>(`${CONNECTIVITY_URL}/pipelines/${name}/runs`),
  setPipelineSchedule: (name: string, scheduleIntervalMinutes: number | null) =>
    api.post<PipelineDefinition>(`${CONNECTIVITY_URL}/pipelines/${name}/schedule`, {
      schedule_interval_minutes: scheduleIntervalMinutes,
    }),
};
