import { useSuspenseQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { identityApi, type AccessRelation } from "../identity";
import { queryKeys } from "../queryKeys";

export function usePrincipals() {
  return useSuspenseQuery({ queryKey: queryKeys.principals(), queryFn: identityApi.listPrincipals });
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
    mutationFn: ({ projectName, principalUrn, relation }: { projectName: string; principalUrn: string; relation: AccessRelation }) =>
      identityApi.grantProjectAccess(projectName, principalUrn, relation),
  });
}

export function useRevokeProjectAccess() {
  return useMutation({
    mutationFn: ({ projectName, principalUrn, relation }: { projectName: string; principalUrn: string; relation: AccessRelation }) =>
      identityApi.revokeProjectAccess(projectName, principalUrn, relation),
  });
}
