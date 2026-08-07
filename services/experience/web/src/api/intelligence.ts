import { INTELLIGENCE_URL } from "./config";
import { api } from "./client";

export interface ToolDefinition {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export const intelligenceApi = {
  listTools: () => api.get<ToolDefinition[]>(`${INTELLIGENCE_URL}/tools`),
};
