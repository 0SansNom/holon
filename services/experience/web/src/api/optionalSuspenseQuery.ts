import {
  skipToken,
  useSuspenseQuery,
  type QueryKey,
  type UseSuspenseQueryOptions,
} from "@tanstack/react-query";

/** Typed wrapper — `skipToken` + `useSuspenseQuery` needs a cast for inference. */
export function useOptionalSuspenseQuery<T>(
  enabled: boolean,
  queryKey: QueryKey,
  queryFn: () => Promise<T>,
) {
  return useSuspenseQuery({
    queryKey,
    queryFn: enabled ? queryFn : skipToken,
  } as UseSuspenseQueryOptions<T>);
}
