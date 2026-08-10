import { useSuspenseQuery } from "@tanstack/react-query";
import { intelligenceApi } from "../intelligence";
import { queryKeys } from "../queryKeys";

export function useTools() {
  return useSuspenseQuery({ queryKey: queryKeys.tools(), queryFn: intelligenceApi.listTools });
}
