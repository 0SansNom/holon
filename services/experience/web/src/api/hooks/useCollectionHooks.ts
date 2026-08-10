import { useSuspenseQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { collectionsApi } from "../collections";
import { queryKeys } from "../queryKeys";
import { useOptionalSuspenseQuery } from "../optionalSuspenseQuery";

export function useCollections() {
  return useSuspenseQuery({ queryKey: queryKeys.collections(), queryFn: collectionsApi.list });
}

export function useCollection(id: number | undefined) {
  return useOptionalSuspenseQuery(id !== undefined, queryKeys.collection(id as number), () => collectionsApi.get(id as number));
}

export function useCreateCollection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, description }: { name: string; description?: string }) =>
      collectionsApi.create(name, description),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.collections() }),
  });
}

export function useDeleteCollection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => collectionsApi.delete(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.collections() }),
  });
}

export function useResourceCollections(urn: string) {
  return useOptionalSuspenseQuery(!!urn, queryKeys.resourceCollections(urn), () => collectionsApi.listForResource(urn));
}

export function useToggleCollectionMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ collectionId, urn, member }: { collectionId: number; urn: string; member: boolean }) =>
      member ? collectionsApi.addMember(collectionId, urn) : collectionsApi.removeMember(collectionId, urn),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.resourceCollections(variables.urn) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.collection(variables.collectionId) });
    },
  });
}
