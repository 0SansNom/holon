import { IDENTITY_URL } from "./config";
import { api } from "./client";

// The browser's sign-in/out path — `POST /login` sets the `holon_session`
// HttpOnly cookie server-side and returns no token in the body (see
// `services/identity/app/main.py`'s `/login`); `POST /token` still exists
// for CLI/script/service-to-service use but is deliberately not called
// from the frontend anymore.
export async function login(principalUrn: string, clientSecret: string): Promise<void> {
  await api.post<{ status: string }>(`${IDENTITY_URL}/login`, {
    principal_urn: principalUrn,
    client_secret: clientSecret,
  });
}

export async function logout(): Promise<void> {
  await api.post<{ status: string }>(`${IDENTITY_URL}/logout`);
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

  whoami: () => api.get<IdentityPrincipal>(`${IDENTITY_URL}/whoami`),

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
