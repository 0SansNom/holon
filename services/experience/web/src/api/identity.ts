import { IDENTITY_URL } from "./config";
import { api } from "./client";

interface TokenResponse {
  access_token: string;
}

export async function mintToken(principalUrn: string, clientSecret: string): Promise<string> {
  const response = await api.post<TokenResponse>(`${IDENTITY_URL}/token`, {
    principal_urn: principalUrn,
    client_secret: clientSecret,
  });
  return response.access_token;
}

export interface IdentityPrincipal {
  urn: string;
  type: "user" | "agent" | "service_account";
  tenant_id: string;
  display_name: string;
  on_behalf_of: string | null;
  country: string | null;
}

export interface Project {
  urn: string;
  tenant_id: string;
  workspace_id: string;
  name: string;
  created_at: string;
}

export type AccessRelation = "viewer" | "editor" | "admin";

export const identityApi = {
  listPrincipals: () => api.get<IdentityPrincipal[]>(`${IDENTITY_URL}/principals`),

  listProjects: () => api.get<Project[]>(`${IDENTITY_URL}/projects`),
  createProject: (name: string) => api.post<Project>(`${IDENTITY_URL}/projects`, { name }),

  grantWorkspaceAccess: (principalUrn: string, relation: AccessRelation) =>
    api.post<{ status: string }>(`${IDENTITY_URL}/principals/${encodeURIComponent(principalUrn)}/access/grant`, { relation }),
  revokeWorkspaceAccess: (principalUrn: string, relation: AccessRelation) =>
    api.post<{ status: string }>(`${IDENTITY_URL}/principals/${encodeURIComponent(principalUrn)}/access/revoke`, { relation }),

  grantProjectAccess: (projectName: string, principalUrn: string, relation: AccessRelation) =>
    api.post<{ status: string }>(
      `${IDENTITY_URL}/projects/${projectName}/principals/${encodeURIComponent(principalUrn)}/access/grant`,
      { relation },
    ),
  revokeProjectAccess: (projectName: string, principalUrn: string, relation: AccessRelation) =>
    api.post<{ status: string }>(
      `${IDENTITY_URL}/projects/${projectName}/principals/${encodeURIComponent(principalUrn)}/access/revoke`,
      { relation },
    ),
};
