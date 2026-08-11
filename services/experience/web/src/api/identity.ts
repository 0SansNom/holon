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

export interface Tenant {
  tenant_id: string;
  display_name: string;
  status: string;
}

export interface Workspace {
  workspace_id: string;
  tenant_id: string;
  display_name: string;
  status: string;
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
  createPrincipal: (body: {
    tenant_id: string;
    type: "user" | "agent" | "service_account";
    local_name: string;
    display_name: string;
    country?: string | null;
  }) =>
    api.post<IdentityPrincipal & { client_secret?: string; status: string }>(`${IDENTITY_URL}/principals`, body),
  setPrincipalStatus: (principalUrn: string, status: "active" | "disabled") =>
    api.post<{ urn: string; status: string }>(
      `${IDENTITY_URL}/principals/${encodeURIComponent(principalUrn)}/status`,
      { status },
    ),

  listTenants: () => api.get<Tenant[]>(`${IDENTITY_URL}/tenants`),
  createTenant: (tenant_id: string, display_name: string) =>
    api.post<Tenant>(`${IDENTITY_URL}/tenants`, { tenant_id, display_name }),
  setTenantStatus: (tenantId: string, status: "active" | "disabled") =>
    api.post<Tenant>(`${IDENTITY_URL}/tenants/${encodeURIComponent(tenantId)}/status`, { status }),

  listWorkspaces: (tenantId?: string) =>
    api.get<Workspace[]>(
      tenantId ? `${IDENTITY_URL}/workspaces?tenant_id=${encodeURIComponent(tenantId)}` : `${IDENTITY_URL}/workspaces`,
    ),
  createWorkspace: (tenant_id: string, workspace_id: string, display_name: string, initial_admin_urn?: string) =>
    api.post<Workspace>(`${IDENTITY_URL}/workspaces`, {
      tenant_id,
      workspace_id,
      display_name,
      ...(initial_admin_urn ? { initial_admin_urn } : {}),
    }),

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

  /** OIDC: returns authorize URL when SSO is configured; 404 when disabled. */
  oidcStart: () => api.get<{ authorize_url: string }>(`${IDENTITY_URL}/oidc/login`),
};
