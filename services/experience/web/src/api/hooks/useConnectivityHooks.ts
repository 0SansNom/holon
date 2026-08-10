import { useSuspenseQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { connectivityApi, type RegisterSourceRequest, type RegisterConnectionRequest } from "../connectivity";
import { queryKeys } from "../queryKeys";

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
