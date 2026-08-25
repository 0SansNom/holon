import { useQuery, type QueryKey } from "@tanstack/react-query";

/**
 * Optional query that must not suspend. `useSuspenseQuery` forces
 * `enabled: true` and rejects `skipToken` (TanStack Query 5.101), which
 * threw `Missing queryFn` whenever a caller passed an empty name
 * (`useObjects("")`, draft application dashboard, …).
 */
export function useOptionalSuspenseQuery<T>(
  enabled: boolean,
  queryKey: QueryKey,
  queryFn: () => Promise<T>,
) {
  return useQuery({
    queryKey,
    queryFn,
    enabled,
  });
}
