import { EXPERIENCE_URL } from "./config";
import { api } from "./client";

export interface ProjectPin {
  project_urn: string;
  resource_urn: string;
  tenant_id: string;
  pinned_by_urn: string;
  pinned_at: string;
}

export const projectPinsApi = {
  list: (projectUrn: string) =>
    api.get<ProjectPin[]>(`${EXPERIENCE_URL}/api/projects/${encodeURIComponent(projectUrn)}/pins`),
  pin: (projectUrn: string, resourceUrn: string) =>
    api.post<{ status: string }>(
      `${EXPERIENCE_URL}/api/projects/${encodeURIComponent(projectUrn)}/pins/${encodeURIComponent(resourceUrn)}`,
    ),
  unpin: (projectUrn: string, resourceUrn: string) =>
    api.delete<{ status: string }>(
      `${EXPERIENCE_URL}/api/projects/${encodeURIComponent(projectUrn)}/pins/${encodeURIComponent(resourceUrn)}`,
    ),
};
