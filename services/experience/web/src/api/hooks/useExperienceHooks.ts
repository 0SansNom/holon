import { useQuery, useSuspenseQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { experienceApi, type ApplicationDefinition } from "../experience";
import { queryKeys } from "../queryKeys";
import { useOptionalSuspenseQuery } from "../optionalSuspenseQuery";

export function useApplications() {
  return useSuspenseQuery({ queryKey: queryKeys.applications(), queryFn: experienceApi.listApplications });
}

/** Non-suspense — ApplicationPage treats 404 as "no draft yet" instead of erroring. */
export function useApplicationOptional(name: string) {
  return useQuery({
    queryKey: queryKeys.application(name),
    queryFn: () => experienceApi.getApplication(name),
    enabled: !!name,
    retry: false,
  });
}

export function useApplication(name: string) {
  return useOptionalSuspenseQuery(!!name, queryKeys.application(name), () => experienceApi.getApplication(name));
}

export function useApplicationDashboard(name: string | undefined) {
  return useOptionalSuspenseQuery(!!name, queryKeys.applicationDashboard(name as string), () =>
    experienceApi.getDashboard(name as string),
  );
}

export function useObjectAppData(name: string | undefined) {
  return useOptionalSuspenseQuery(!!name, queryKeys.objectAppData(name as string), () =>
    experienceApi.listObjectAppData(name as string),
  );
}

export function useObjectAppDetail(name: string | undefined, id: string | number | undefined) {
  return useOptionalSuspenseQuery(
    !!name && id !== undefined,
    queryKeys.objectAppDetail(name as string, id as string | number),
    () => experienceApi.getObjectAppDetail(name as string, id as string | number),
  );
}

export function useInvokeObjectAppAction(name: string) {
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
    }) => experienceApi.invokeObjectAppAction(name, id, actionName, { reason, parameters }),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.objectAppDetail(name, variables.id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.objectAppData(name) });
    },
  });
}

export function useSaveApplication(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (definition: ApplicationDefinition) => experienceApi.createOrUpdateApplication(name, definition),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.application(name) });
    },
  });
}

export function useSetApplicationProject(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (projectUrn: string | null) => experienceApi.setApplicationProject(name, projectUrn),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.application(name) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.applications() });
    },
  });
}

export function usePromoteApplication(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => experienceApi.promoteApplication(name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.application(name) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.applicationDashboard(name) });
    },
  });
}
