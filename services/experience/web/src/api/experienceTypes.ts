export interface ActionRef {
  action: string;
  riskClass: string;
}

export interface ObjectAppSurface {
  type: "objectApp";
  objectType: string;
  route?: string;
}

export interface DashboardWidgetSurface {
  component: "kpi" | "table" | string;
  objectType?: string;
  label?: string;
}

export interface DashboardSurface {
  type: "dashboard";
  route?: string;
  widgets?: DashboardWidgetSurface[];
}

export interface AgentAppSurface {
  type: "agentApp";
  route?: string;
  systemPrompt?: string;
  tools?: string[];
  budget?: {
    max_iterations?: number;
    max_tool_calls?: number;
    max_tokens?: number;
  };
}

/** Surfaces the builder knows how to round-trip; other surface types are preserved as-is. */
export type KnownApplicationSurface = ObjectAppSurface | DashboardSurface | AgentAppSurface;

export type ApplicationSurface = KnownApplicationSurface | ({ type: string } & Record<string, unknown>);

export interface TableBinding {
  component: "table";
  objectType: string;
}

export interface DetailBinding {
  component: "detail";
  objectType: string;
}

export type ApplicationBinding = TableBinding | DetailBinding | Record<string, unknown>;

export interface ApplicationDefinition {
  surfaces: ApplicationSurface[];
  bindings: ApplicationBinding[];
  actionRefs: ActionRef[];
}

export function isObjectAppSurface(surface: ApplicationSurface): surface is ObjectAppSurface {
  return surface.type === "objectApp";
}

export function isDashboardSurface(surface: ApplicationSurface): surface is DashboardSurface {
  return surface.type === "dashboard";
}

export function isAgentAppSurface(surface: ApplicationSurface): surface is AgentAppSurface {
  return surface.type === "agentApp";
}
