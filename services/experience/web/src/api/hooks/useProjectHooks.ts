import { useMutation, useQueryClient } from "@tanstack/react-query";
import { projectPinsApi } from "../projects";
import { queryKeys } from "../queryKeys";
import { useOptionalSuspenseQuery } from "../optionalSuspenseQuery";

export function useProjectPins(projectUrn: string | undefined) {
  return useOptionalSuspenseQuery(!!projectUrn, queryKeys.projectPins(projectUrn as string), () =>
    projectPinsApi.list(projectUrn as string),
  );
}

export function usePinResource(projectUrn: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (resourceUrn: string) => projectPinsApi.pin(projectUrn, resourceUrn),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.projectPins(projectUrn) }),
  });
}

export function useUnpinResource(projectUrn: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (resourceUrn: string) => projectPinsApi.unpin(projectUrn, resourceUrn),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.projectPins(projectUrn) }),
  });
}
