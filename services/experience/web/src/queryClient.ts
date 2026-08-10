import { QueryClient, QueryCache, hashKey } from "@tanstack/react-query";
import { useAuthStore } from "./store/auth";
import { ApiError } from "./api/client";

/** Query data is authorization-sensitive, so cache entries are scoped to the
 * principal that created them. */
export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error, query) => {
      // 401 is already handled centrally by client.ts (redirect /login) —
      // skip it here to avoid duplicate noise in the console.
      if (error instanceof ApiError && error.status === 401) return;
      console.error(`[Query error] key=${JSON.stringify(query.queryKey)}`, error);
    },
  }),
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5_000,
      queryKeyHashFn: (queryKey) => hashKey([useAuthStore.getState().session?.principal.urn ?? "anonymous", queryKey]),
    },
  },
});
