import { useQuery } from "@tanstack/react-query";
import { intelligenceApi } from "../intelligence";
import { queryKeys } from "../queryKeys";

/** Agent-tool catalog is optional on Application Builder. Intelligence
 * 500s (e.g. Knowledge 401 when only a session cookie is forwarded)
 * must not take down the whole application page. */
export function useTools() {
  return useQuery({
    queryKey: queryKeys.tools(),
    queryFn: intelligenceApi.listTools,
    retry: false,
  });
}
