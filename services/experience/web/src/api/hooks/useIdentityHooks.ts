import { useSuspenseQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { identityApi, type AccessRelation } from "../identity";
import { queryKeys } from "../queryKeys";

export function usePrincipals() {
  return useSuspenseQuery({ queryKey: queryKeys.principals(), queryFn: identityApi.listPrincipals });
}

export function useTenants() {
  return useSuspenseQuery({ queryKey: queryKeys.tenants(), queryFn: identityApi.listTenants });
}

export function useWorkspaces(tenantId?: string) {
  return useSuspenseQuery({
    queryKey: queryKeys.workspaces(tenantId),
    queryFn: () => identityApi.listWorkspaces(tenantId),
  });
}

export function useCreateTenant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tenantId, displayName }: { tenantId: string; displayName: string }) =>
      identityApi.createTenant(tenantId, displayName),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.tenants() }),
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      tenantId,
      workspaceId,
      displayName,
      initialAdminUrn,
    }: {
      tenantId: string;
      workspaceId: string;
      displayName: string;
      initialAdminUrn?: string;
    }) => identityApi.createWorkspace(tenantId, workspaceId, displayName, initialAdminUrn),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["workspaces"] }),
  });
}

export function useCreatePrincipal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      tenant_id: string;
      type: "user" | "agent" | "service_account";
      local_name: string;
      display_name: string;
      country?: string | null;
    }) => identityApi.createPrincipal(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.principals() }),
  });
}

export function useSetPrincipalStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ principalUrn, status }: { principalUrn: string; status: "active" | "disabled" }) =>
      identityApi.setPrincipalStatus(principalUrn, status),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.principals() }),
  });
}

export function useProjects() {
  return useSuspenseQuery({ queryKey: queryKeys.projects(), queryFn: identityApi.listProjects });
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => identityApi.createProject(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.projects() }),
  });
}

export function useGrantWorkspaceAccess() {
  return useMutation({
    mutationFn: ({ principalUrn, relation }: { principalUrn: string; relation: AccessRelation }) =>
      identityApi.grantWorkspaceAccess(principalUrn, relation),
  });
}

export function useRevokeWorkspaceAccess() {
  return useMutation({
    mutationFn: ({ principalUrn, relation }: { principalUrn: string; relation: AccessRelation }) =>
      identityApi.revokeWorkspaceAccess(principalUrn, relation),
  });
}

export function useGrantProjectAccess() {
  return useMutation({
    mutationFn: ({
      projectName,
      principalUrn,
      relation,
    }: {
      projectName: string;
      principalUrn: string;
      relation: AccessRelation;
    }) => identityApi.grantProjectAccess(projectName, principalUrn, relation),
  });
}

export function useRevokeProjectAccess() {
  return useMutation({
    mutationFn: ({
      projectName,
      principalUrn,
      relation,
    }: {
      projectName: string;
      principalUrn: string;
      relation: AccessRelation;
    }) => identityApi.revokeProjectAccess(projectName, principalUrn, relation),
  });
}
