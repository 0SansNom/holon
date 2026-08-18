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
