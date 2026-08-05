import { EXPERIENCE_URL } from "./config";
import { api } from "./client";

export interface ApplicationDefinition {
  surfaces: Array<Record<string, unknown>>;
  bindings: Array<Record<string, unknown>>;
  actionRefs: Array<{ action: string; riskClass: string }>;
}

export interface Application {
  id: number;
  tenant_id: string;
  name: string;
  version: number;
  definition: ApplicationDefinition;
  dependencies: { objectTypes: string[]; actions: string[] };
  status: "draft" | "promoted";
  created_at: string;
  promoted_at: string | null;
}

export interface DashboardWidget {
  label: string;
  component: string;
  value?: number;
  rows?: Array<Record<string, unknown>>;
  iframeUrl?: string | null;
}

export interface DashboardResponse {
  applicationName: string;
  widgets: DashboardWidget[];
}

export const experienceApi = {
  listApplications: () => api.get<Application[]>(`${EXPERIENCE_URL}/api/applications`),
  createOrUpdateApplication: (name: string, definition: ApplicationDefinition) =>
    api.post<Application>(`${EXPERIENCE_URL}/api/applications/${name}`, { definition }),
  getApplication: (name: string) => api.get<Application>(`${EXPERIENCE_URL}/api/applications/${name}`),
  promoteApplication: (name: string) => api.post<Application>(`${EXPERIENCE_URL}/api/applications/${name}/promote`),

  getDashboard: (name: string) => api.get<DashboardResponse>(`${EXPERIENCE_URL}/api/applications/${name}/dashboard`),

  listObjectAppData: (name: string) => api.get<Array<Record<string, unknown>>>(`${EXPERIENCE_URL}/api/applications/${name}/data`),
  getObjectAppDetail: (name: string, id: string | number) =>
    api.get<Record<string, unknown>>(`${EXPERIENCE_URL}/api/applications/${name}/data/${id}`),
  invokeObjectAppAction: (name: string, id: string | number, actionName: string, body: { reason: string }) =>
    api.post<Record<string, unknown>>(`${EXPERIENCE_URL}/api/applications/${name}/data/${id}/actions/${actionName}`, body),

  getForm: (name: string) =>
    api.get<{ action: string; fields: Array<{ name: string; type: string; required: boolean }> }>(
      `${EXPERIENCE_URL}/api/applications/${name}/form`,
    ),
  submitForm: (name: string, id: string | number, body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`${EXPERIENCE_URL}/api/applications/${name}/form/${id}`, body),
};
