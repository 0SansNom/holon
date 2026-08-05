import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { knowledgeApi } from "./knowledge";
import { experienceApi, type ApplicationDefinition } from "./experience";
import { useAuthStore } from "../store/auth";

function useIsAuthed(): boolean {
  return useAuthStore((s) => s.session !== null);
}

export function useObjectTypes() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["objectTypes"], queryFn: knowledgeApi.listObjectTypes, enabled });
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

export function useActions() {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["actions"], queryFn: knowledgeApi.listActions, enabled });
}

export function useLineage(urn: string | undefined) {
  const enabled = useIsAuthed();
  return useQuery({
    queryKey: ["lineage", urn],
    queryFn: () => knowledgeApi.getLineage(urn as string),
    enabled: enabled && !!urn,
  });
}

export function useSearch(q: string) {
  const enabled = useIsAuthed();
  return useQuery({ queryKey: ["search", q], queryFn: () => knowledgeApi.search(q), enabled: enabled && q.length > 0 });
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
