import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { knowledgeApi } from "./knowledge";
import { experienceApi, type ApplicationDefinition } from "./experience";
import { intelligenceApi } from "./intelligence";
import { identityApi, type AccessRelation } from "./identity";
import { connectivityApi, type RegisterSourceRequest, type RegisterConnectionRequest } from "./connectivity";
import { useAuthStore } from "../store/auth";

function useIsAuthed(): boolean {
  return useAuthStore((s) => s.session !== null);
}

export function useObjectTypes() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["objectTypes"], queryFn: knowledgeApi.listObjectTypes, enabled });
}

export function useDatasetPreview(datasetName: string) {
  const enabled = useIsAuthed();
  return useQuery({
    queryKey: ["datasetPreview", datasetName],
    queryFn: () => knowledgeApi.previewDataset(datasetName),
    enabled: enabled && !!datasetName,
    retry: false,
  });
}

export function useCreateObjectType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      source_dataset_urn: string;
      property_mapping: Record<string, string>;
      description?: string;
      column_classification?: Record<string, string>;
    }) => knowledgeApi.createObjectType(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["objectTypes"] }),
  });
}

export function useObjectType(name: string) {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["objectType", name], queryFn: () => knowledgeApi.getObjectType(name), enabled: enabled && !!name });
}

export function useObjectTypeVersions(name: string) {
  const enabled = useIsAuthed();
  return useQuery({
    queryKey: ["objectTypeVersions", name],
    queryFn: () => knowledgeApi.listObjectTypeVersions(name),
    enabled: enabled && !!name,
  });
}

export function useObjects(objectType: string) {
  const enabled = useIsAuthed();
  return useQuery({
    queryKey: ["objects", objectType],
    queryFn: () => knowledgeApi.listObjects(objectType),
    enabled: enabled && !!objectType,
  });
}

export function useObject(objectType: string, id: string | number | undefined) {
  const enabled = useIsAuthed();
  return useQuery({
    queryKey: ["object", objectType, id],
    queryFn: () => knowledgeApi.getObject(objectType, id as string | number),
    enabled: enabled && !!objectType && id !== undefined,
  });
}

export function useRelationTypes() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["relationTypes"], queryFn: knowledgeApi.listRelationTypes, enabled });
}

export function useCreateRelationType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createRelationType>[0]) => knowledgeApi.createRelationType(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["relationTypes"] }),
  });
}

export function useActions() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["actions"], queryFn: knowledgeApi.listActions, enabled });
}

export function useValueTypes() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["valueTypes"], queryFn: knowledgeApi.listValueTypes, enabled });
}

export function useCreateValueType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createValueType>[0]) => knowledgeApi.createValueType(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["valueTypes"] }),
  });
}

export function useSharedPropertyTypes() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["sharedPropertyTypes"], queryFn: knowledgeApi.listSharedPropertyTypes, enabled });
}

export function useCreateSharedPropertyType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createSharedPropertyType>[0]) =>
      knowledgeApi.createSharedPropertyType(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["sharedPropertyTypes"] }),
  });
}

export function useActionTypes() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["actionTypes"], queryFn: knowledgeApi.listActionTypes, enabled });
}

export function useCreateActionType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createActionType>[0]) => knowledgeApi.createActionType(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["actionTypes"] });
      void queryClient.invalidateQueries({ queryKey: ["actions"] });
    },
  });
}

export function useInterfaces() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["interfaces"], queryFn: knowledgeApi.listInterfaces, enabled });
}

export function useCreateInterface() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createInterface>[0]) => knowledgeApi.createInterface(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["interfaces"] }),
  });
}

export function useMarkings() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["markings"], queryFn: knowledgeApi.listMarkings, enabled });
}

export function useCreateMarking() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createMarking>[0]) => knowledgeApi.createMarking(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["markings"] }),
  });
}

export function useProposeObjectTypeVersion(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.proposeObjectTypeVersion>[1]) =>
      knowledgeApi.proposeObjectTypeVersion(name, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["objectTypeVersions", name] }),
  });
}

export function usePublishObjectTypeVersion(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (version: number) => knowledgeApi.publishObjectTypeVersion(name, version),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["objectTypeVersions", name] });
      void queryClient.invalidateQueries({ queryKey: ["objectType", name] });
      void queryClient.invalidateQueries({ queryKey: ["objectTypes"] });
    },
  });
}

export function useTools() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["tools"], queryFn: intelligenceApi.listTools, enabled });
}

export function useLineage(urn: string | undefined) {
  const enabled = useIsAuthed();
  return useQuery({
    queryKey: ["lineage", urn],
    queryFn: () => knowledgeApi.getLineage(urn as string),
    enabled: enabled && !!urn,
  });
}

export function useObjectGraph(objectType: string, id: string | number | undefined, hops = 2) {
  const enabled = useIsAuthed();
  return useQuery({
    queryKey: ["objectGraph", objectType, id, hops],
    queryFn: () => knowledgeApi.getObjectGraph(objectType, id as string | number, hops),
    enabled: enabled && !!objectType && id !== undefined,
  });
}

export function useSearch(q: string, options?: { objectType?: string; from?: number; size?: number }) {
  const enabled = useIsAuthed();
  return useQuery({
    queryKey: ["search", q, options?.objectType, options?.from, options?.size],
    queryFn: () => knowledgeApi.search(q, options),
    enabled: enabled && q.length > 0,
  });
}

export function useGlossary() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["glossary"], queryFn: knowledgeApi.listGlossary, enabled });
}

export function useApplications() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["applications"], queryFn: experienceApi.listApplications, enabled });
}

export function useApplication(name: string) {
  const enabled = useIsAuthed();
  return useQuery({
    queryKey: ["application", name],
    queryFn: () => experienceApi.getApplication(name),
    enabled: enabled && !!name,
    retry: false,
  });
}

export function useApplicationDashboard(name: string | undefined) {
  const enabled = useIsAuthed();
  return useQuery({
    queryKey: ["applicationDashboard", name],
    queryFn: () => experienceApi.getDashboard(name as string),
    enabled: enabled && !!name,
  });
}

export function useSaveApplication(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (definition: ApplicationDefinition) => experienceApi.createOrUpdateApplication(name, definition),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["application", name] });
    },
  });
}

export function usePromoteApplication(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => experienceApi.promoteApplication(name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["application", name] });
      void queryClient.invalidateQueries({ queryKey: ["applicationDashboard", name] });
    },
  });
}

export function useInvokeAction(objectType: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, actionName, reason }: { id: string | number; actionName: string; reason: string }) =>
      knowledgeApi.invokeAction(objectType, id, actionName, reason),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["objects", objectType] });
      void queryClient.invalidateQueries({ queryKey: ["object", objectType, variables.id] });
    },
  });
}

// --- Admin (principal/workspace/project management) ---

export function usePrincipals() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["principals"], queryFn: identityApi.listPrincipals, enabled });
}

export function useProjects() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["projects"], queryFn: identityApi.listProjects, enabled });
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => identityApi.createProject(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["projects"] }),
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

export function useSources() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["sources"], queryFn: connectivityApi.listSources, enabled });
}

export function useRegisterSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RegisterSourceRequest) => connectivityApi.registerSource(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });
}

export function useSyncDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dataset: string) => connectivityApi.sync(dataset),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["syncs"] }),
  });
}

export function useSyncs() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["syncs"], queryFn: connectivityApi.listSyncs, enabled });
}

export function useDisableSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => connectivityApi.disableSource(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });
}

export function useEnableSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => connectivityApi.enableSource(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });
}

export function useDeleteSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => connectivityApi.deleteSource(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["sources"] }),
  });
}

export function useConnections() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["connections"], queryFn: connectivityApi.listConnections, enabled });
}

export function useRegisterConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RegisterConnectionRequest) => connectivityApi.registerConnection(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["connections"] }),
  });
}

export function useDeleteConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => connectivityApi.deleteConnection(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["connections"] }),
  });
}
