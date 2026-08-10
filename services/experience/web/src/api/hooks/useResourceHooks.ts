import { useSuspenseQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { resourceApi } from "../resources";
import { queryKeys } from "../queryKeys";

export function useResourceTags() {
  return useSuspenseQuery({ queryKey: queryKeys.resourceTags(), queryFn: () => resourceApi.list() });
}

export function useSetResourceTags() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ urn, tags }: { urn: string; tags: string[] }) => resourceApi.setTags(urn, tags),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.resourceTags() }),
  });
}

export function useSetFeatured() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ urn, featured }: { urn: string; featured: boolean }) =>
      featured ? resourceApi.setFeatured(urn) : resourceApi.unsetFeatured(urn),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.resourceTags() }),
  });
}
