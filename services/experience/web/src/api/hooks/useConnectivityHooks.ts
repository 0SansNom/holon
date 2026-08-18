import { useSuspenseQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  connectivityApi,
  type RegisterSourceRequest,
  type RegisterConnectionRequest,
  type RegisterWriteTargetRequest,
} from "../connectivity";
import { queryKeys } from "../queryKeys";
import { useOptionalSuspenseQuery } from "../optionalSuspenseQuery";

export function useSources() {
  return useSuspenseQuery({ queryKey: queryKeys.sources(), queryFn: connectivityApi.listSources });
}

export function useRegisterSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RegisterSourceRequest) => connectivityApi.registerSource(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.sources() }),
  });
}

export function useSyncDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dataset: string) => connectivityApi.sync(dataset),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.syncs() }),
  });
}

export function useSyncs() {
  return useSuspenseQuery({ queryKey: queryKeys.syncs(), queryFn: connectivityApi.listSyncs });
}

export function useDisableSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => connectivityApi.disableSource(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.sources() }),
  });
}

export function useEnableSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => connectivityApi.enableSource(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.sources() }),
  });
}

export function useDeleteSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => connectivityApi.deleteSource(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.sources() }),
  });
}

export function usePlugins() {
  return useSuspenseQuery({ queryKey: queryKeys.plugins(), queryFn: connectivityApi.listPlugins });
}

export function useDisablePlugin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => connectivityApi.disablePlugin(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.plugins() }),
  });
}

export function useEnablePlugin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => connectivityApi.enablePlugin(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.plugins() }),
  });
}

export function useSetPluginSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, scheduleIntervalMinutes }: { name: string; scheduleIntervalMinutes: number | null }) =>
      connectivityApi.setPluginSchedule(name, scheduleIntervalMinutes),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.plugins() }),
  });
}

export function useConnections() {
  return useSuspenseQuery({ queryKey: queryKeys.connections(), queryFn: connectivityApi.listConnections });
}

export function useRegisterConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RegisterConnectionRequest) => connectivityApi.registerConnection(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.connections() }),
  });
}

export function useDeleteConnection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => connectivityApi.deleteConnection(name),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.connections() }),
  });
}

export function useWriteTargets() {
  return useSuspenseQuery({ queryKey: queryKeys.writeTargets(), queryFn: connectivityApi.listWriteTargets });
}

export function useRegisterWriteTarget() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RegisterWriteTargetRequest) => connectivityApi.registerWriteTarget(body),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.writeTargets() }),
  });
}

export function useDeleteWriteTarget() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (datasetName: string) => connectivityApi.deleteWriteTarget(datasetName),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.writeTargets() }),
  });
}

export function usePipelines() {
  return useSuspenseQuery({ queryKey: queryKeys.pipelines(), queryFn: connectivityApi.listPipelines });
}

export function usePipeline(name: string) {
  return useOptionalSuspenseQuery(!!name, queryKeys.pipeline(name), () => connectivityApi.getPipeline(name));
}

export function usePipelineRuns(name: string) {
  return useQuery({
    queryKey: queryKeys.pipelineRuns(name),
    queryFn: () => connectivityApi.listPipelineRuns(name),
    enabled: !!name,
  });
}

export function useRunPipeline(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => connectivityApi.runPipeline(name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.pipelineRuns(name) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.syncs() });
    },
  });
}

export function useCreatePipeline() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, steps }: { name: string; steps: Parameters<typeof connectivityApi.createPipeline>[1]["steps"] }) =>
      connectivityApi.createPipeline(name, { steps }),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.pipelines() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.pipeline(variables.name) });
    },
  });
}

export function useDeletePipeline() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => connectivityApi.deletePipeline(name),
    onSuccess: (_data, name) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.pipelines() });
      void queryClient.removeQueries({ queryKey: queryKeys.pipeline(name) });
      void queryClient.removeQueries({ queryKey: queryKeys.pipelineRuns(name) });
    },
  });
}

export function useSetPipelineSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, scheduleIntervalMinutes }: { name: string; scheduleIntervalMinutes: number | null }) =>
      connectivityApi.setPipelineSchedule(name, scheduleIntervalMinutes),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.pipelines() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.pipeline(variables.name) });
    },
  });
}
