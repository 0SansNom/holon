import { useQuery, useSuspenseQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { knowledgeApi } from "../knowledge";
import { queryKeys, BRANCH_KIND_LIST_QUERY_KEY, type BranchKind } from "../queryKeys";
import { useOptionalSuspenseQuery } from "../optionalSuspenseQuery";

export type { BranchKind };

export function useObjectTypes() {
  return useSuspenseQuery({ queryKey: queryKeys.objectTypes(), queryFn: knowledgeApi.listObjectTypes });
}

export function useDatasetPreview(datasetName: string) {
  return useQuery({
    queryKey: queryKeys.datasetPreview(datasetName),
    queryFn: () => knowledgeApi.previewDataset(datasetName),
    enabled: !!datasetName,
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
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.objectTypes() }),
  });
}

export function useObjectType(name: string) {
  return useOptionalSuspenseQuery(!!name, queryKeys.objectType(name), () => knowledgeApi.getObjectType(name));
}

export function useObjectTypeVersions(name: string) {
  return useOptionalSuspenseQuery(!!name, queryKeys.objectTypeVersions(name), () =>
    knowledgeApi.listObjectTypeVersions(name),
  );
}

export function useObjects(objectType: string) {
  return useOptionalSuspenseQuery(!!objectType, queryKeys.objects(objectType), () => knowledgeApi.listObjects(objectType));
}

export function useObject(objectType: string, id: string | number | undefined) {
  return useOptionalSuspenseQuery(
    !!objectType && id !== undefined,
    queryKeys.object(objectType, id as string | number),
    () => knowledgeApi.getObject(objectType, id as string | number),
  );
}

export function useRelationTypes() {
  return useSuspenseQuery({ queryKey: queryKeys.relationTypes(), queryFn: knowledgeApi.listRelationTypes });
}

export function useCreateRelationType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createRelationType>[0]) => knowledgeApi.createRelationType(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.relationTypes() }),
  });
}

export function useUpdateRelationType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: Parameters<typeof knowledgeApi.updateRelationType>[1] }) =>
      knowledgeApi.updateRelationType(name, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.relationTypes() }),
  });
}

export function useActions() {
  return useSuspenseQuery({ queryKey: queryKeys.actions(), queryFn: knowledgeApi.listActions });
}

export function useValueTypes() {
  return useSuspenseQuery({ queryKey: queryKeys.valueTypes(), queryFn: knowledgeApi.listValueTypes });
}

export function useCreateValueType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createValueType>[0]) => knowledgeApi.createValueType(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.valueTypes() }),
  });
}

export function useUpdateValueType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: Parameters<typeof knowledgeApi.updateValueType>[1] }) =>
      knowledgeApi.updateValueType(name, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.valueTypes() }),
  });
}

export function useSharedPropertyTypes() {
  return useSuspenseQuery({ queryKey: queryKeys.sharedPropertyTypes(), queryFn: knowledgeApi.listSharedPropertyTypes });
}

export function useCreateSharedPropertyType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createSharedPropertyType>[0]) =>
      knowledgeApi.createSharedPropertyType(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.sharedPropertyTypes() }),
  });
}

export function useUpdateSharedPropertyType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ apiName, body }: { apiName: string; body: Parameters<typeof knowledgeApi.updateSharedPropertyType>[1] }) =>
      knowledgeApi.updateSharedPropertyType(apiName, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.sharedPropertyTypes() }),
  });
}

export function useActionTypes() {
  return useSuspenseQuery({ queryKey: queryKeys.actionTypes(), queryFn: knowledgeApi.listActionTypes });
}

export function useCreateActionType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createActionType>[0]) => knowledgeApi.createActionType(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.actionTypes() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.actions() });
    },
  });
}

export function useUpdateActionType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: Parameters<typeof knowledgeApi.updateActionType>[1] }) =>
      knowledgeApi.updateActionType(name, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.actionTypes() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.actions() });
    },
  });
}

export function useInterfaces() {
  return useSuspenseQuery({ queryKey: queryKeys.interfaces(), queryFn: knowledgeApi.listInterfaces });
}

export function useCreateInterface() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createInterface>[0]) => knowledgeApi.createInterface(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.interfaces() }),
  });
}

export function useUpdateInterface() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: Parameters<typeof knowledgeApi.updateInterface>[1] }) =>
      knowledgeApi.updateInterface(name, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.interfaces() }),
  });
}

export function useMarkings() {
  return useSuspenseQuery({ queryKey: queryKeys.markings(), queryFn: knowledgeApi.listMarkings });
}

export function useCreateMarking() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createMarking>[0]) => knowledgeApi.createMarking(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.markings() }),
  });
}

export function useProposeObjectTypeVersion(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.proposeObjectTypeVersion>[1]) =>
      knowledgeApi.proposeObjectTypeVersion(name, body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.objectTypeVersions(name) }),
  });
}

export function usePublishObjectTypeVersion(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (version: number) => knowledgeApi.publishObjectTypeVersion(name, version),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.objectTypeVersions(name) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.objectType(name) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.objectTypes() });
    },
  });
}

export function useBranches(kind: BranchKind, resourceName: string) {
  return useOptionalSuspenseQuery(!!resourceName, queryKeys.branches(kind, resourceName), () =>
    kind === "object_type"
      ? knowledgeApi.listBranches(resourceName)
      : knowledgeApi.listResourceBranches(kind, resourceName),
  );
}

export function useCreateBranch(kind: BranchKind, resourceName: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { branch_name: string; definition: Record<string, unknown> }) =>
      kind === "object_type"
        ? knowledgeApi.createBranch(resourceName, { branch_name: body.branch_name, ...body.definition })
        : knowledgeApi.createResourceBranch(kind, resourceName, { branch_name: body.branch_name, proposed_definition: body.definition }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.branches(kind, resourceName) }),
  });
}

export function useUpdateBranchDraft(kind: BranchKind, resourceName: string, branchName: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (definition: Record<string, unknown>) =>
      kind === "object_type"
        ? knowledgeApi.updateBranchDraft(resourceName, branchName, definition)
        : knowledgeApi.updateResourceBranchDraft(kind, resourceName, branchName, { proposed_definition: definition }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.branches(kind, resourceName) }),
  });
}

export function useReviewBranch(kind: BranchKind, resourceName: string, branchName: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { decision: "approved" | "changes_requested"; note?: string }) =>
      kind === "object_type"
        ? knowledgeApi.reviewBranch(resourceName, branchName, body)
        : knowledgeApi.reviewResourceBranch(kind, resourceName, branchName, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.branches(kind, resourceName) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.branchReviews(kind, resourceName, branchName) });
      void queryClient.invalidateQueries({ queryKey: BRANCH_KIND_LIST_QUERY_KEY[kind] });
      if (kind === "object_type") {
        void queryClient.invalidateQueries({ queryKey: queryKeys.objectType(resourceName) });
        void queryClient.invalidateQueries({ queryKey: queryKeys.objectTypeVersions(resourceName) });
      }
      if (kind === "action_type") {
        void queryClient.invalidateQueries({ queryKey: queryKeys.actions() });
      }
    },
  });
}

export function useBranchReviews(kind: BranchKind, resourceName: string, branchName: string) {
  return useOptionalSuspenseQuery(!!branchName, queryKeys.branchReviews(kind, resourceName, branchName), () =>
    kind === "object_type"
      ? knowledgeApi.listBranchReviews(resourceName, branchName)
      : knowledgeApi.listResourceBranchReviews(kind, resourceName, branchName),
  );
}

export function useLineage(urn: string | undefined) {
  return useOptionalSuspenseQuery(!!urn, queryKeys.lineage(urn as string), () => knowledgeApi.getLineage(urn as string));
}

export function useObjectGraph(objectType: string, id: string | number | undefined, hops = 2) {
  return useOptionalSuspenseQuery(
    !!objectType && id !== undefined,
    queryKeys.objectGraph(objectType, id as string | number, hops),
    () => knowledgeApi.getObjectGraph(objectType, id as string | number, hops),
  );
}

export function useObjectTimeline(objectType: string, id: string | number | undefined) {
  return useOptionalSuspenseQuery(
    !!objectType && id !== undefined,
    queryKeys.objectTimeline(objectType, id as string | number),
    () => knowledgeApi.getObjectTimeline(objectType, id as string | number),
  );
}

export function useRevertActionInvocation(objectType: string, id: string | number | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (invocationId: number) => knowledgeApi.revertActionInvocation(invocationId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.objectTimeline(objectType, id as string | number) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.object(objectType, id as string | number) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.objects(objectType) });
    },
  });
}

/** Progressive disclosure — stays on useQuery so expanding a link doesn't suspend the whole page. */
export function useObjectLinks(objectType: string, id: string | number | undefined, linkName: string, expanded: boolean) {
  return useQuery({
    queryKey: queryKeys.objectLinks(objectType, id as string | number, linkName),
    queryFn: () => knowledgeApi.getObjectLinks(objectType, id as string | number, linkName),
    enabled: expanded && !!objectType && id !== undefined,
  });
}

/** On-demand — only fetched after the user clicks "Run health check". */
export function useOntologyHealthCheck(triggered: boolean) {
  return useQuery({
    queryKey: queryKeys.ontologyHealthCheck(),
    queryFn: knowledgeApi.getOntologyHealthCheck,
    enabled: triggered,
  });
}

export function useObjectTypeGroups() {
  return useSuspenseQuery({ queryKey: queryKeys.objectTypeGroups(), queryFn: knowledgeApi.listObjectTypeGroups });
}

export function useCreateObjectTypeGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof knowledgeApi.createObjectTypeGroup>[0]) => knowledgeApi.createObjectTypeGroup(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.objectTypeGroups() }),
  });
}

/** Search input — only runs when the user has typed a query. */
export function useSearch(q: string, options?: { objectType?: string; from?: number; size?: number }) {
  return useQuery({
    queryKey: queryKeys.search(q, options?.objectType, options?.from, options?.size),
    queryFn: () => knowledgeApi.search(q, options),
    enabled: q.length > 0,
  });
}

export function useGlossary() {
  return useSuspenseQuery({ queryKey: queryKeys.glossary(), queryFn: knowledgeApi.listGlossary });
}

export function useInvokeAction(objectType: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      actionName,
      reason,
      parameters,
    }: {
      id: string | number;
      actionName: string;
      reason: string;
      parameters?: Record<string, unknown>;
    }) => knowledgeApi.invokeAction(objectType, id, actionName, reason, parameters),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.objects(objectType) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.object(objectType, variables.id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.objectTimeline(objectType, variables.id) });
    },
  });
}
