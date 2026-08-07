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

export const connectivityApi = {
  listSources: () => api.get<GenericSource[]>(`${CONNECTIVITY_URL}/sources`),
  registerSource: (body: RegisterSourceRequest) => api.post<GenericSource>(`${CONNECTIVITY_URL}/sources`, body),
  sync: (dataset: string) => api.post<SyncResult>(`${CONNECTIVITY_URL}/sync`, { dataset }),
  disableSource: (name: string) => api.post<GenericSource>(`${CONNECTIVITY_URL}/sources/${name}/disable`),
  enableSource: (name: string) => api.post<GenericSource>(`${CONNECTIVITY_URL}/sources/${name}/enable`),
  deleteSource: (name: string) => api.delete<{ deleted: string }>(`${CONNECTIVITY_URL}/sources/${name}`),
  listSyncs: () => api.get<SyncRun[]>(`${CONNECTIVITY_URL}/syncs`),
  listConnections: () => api.get<GenericConnection[]>(`${CONNECTIVITY_URL}/connections`),
  registerConnection: (body: RegisterConnectionRequest) => api.post<GenericConnection>(`${CONNECTIVITY_URL}/connections`, body),
  deleteConnection: (name: string) => api.delete<{ deleted: string }>(`${CONNECTIVITY_URL}/connections/${name}`),
};
