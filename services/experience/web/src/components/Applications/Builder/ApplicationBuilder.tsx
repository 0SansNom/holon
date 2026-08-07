import { useEffect, useState } from "react";
import { DndContext, type DragEndEvent } from "@dnd-kit/core";
import { arrayMove } from "@dnd-kit/sortable";
import { Button, Callout } from "@blueprintjs/core";
import type { ApplicationDefinition } from "../../../api/experience";
import type { ObjectType, ActionDefinition } from "../../../api/knowledge";
import type { ToolDefinition } from "../../../api/intelligence";
import { WidgetPalette } from "./WidgetPalette";
import { ObjectAppSection, type ObjectAppValue } from "./ObjectAppSection";
import { DashboardSection, type DashboardValue, type DashboardWidgetConfig } from "./DashboardSection";
import { AgentAppSection, type AgentAppValue } from "./AgentAppSection";

interface BuilderState {
  objectApp: ObjectAppValue;
  dashboard: DashboardValue;
  agentApp: AgentAppValue;
}

const DEFAULT_STATE: BuilderState = {
  objectApp: { enabled: false, objectType: "", route: "", actions: [] },
  dashboard: { enabled: false, route: "", widgets: [] },
  agentApp: {
    enabled: false,
    route: "",
    systemPrompt: "",
    tools: [],
    maxIterations: 10,
    maxToolCalls: 25,
    maxTokens: 50_000,
  },
};

// The Builder is a second, opinionated editor over the *same* resource
// Monaco's raw JSON tab edits — it only ever needs to round-trip the
// shapes those surfaces actually use, not the full generality raw JSON
// allows (a hand-authored surface with fields the Builder doesn't know
// about is left alone: this only rewrites the three surface types it
// owns, `definitionToState`/`stateToDefinition` are deliberately lossy
// only in that one direction, never dropping *other* surfaces present).
function definitionToState(definition: ApplicationDefinition): BuilderState {
  const surfaces = definition.surfaces ?? [];
  const objectAppSurface = surfaces.find((s) => s.type === "objectApp") as
    | { objectType?: string; route?: string }
    | undefined;
  const dashboardSurface = surfaces.find((s) => s.type === "dashboard") as
    | { route?: string; widgets?: Array<{ component?: string; objectType?: string; label?: string }> }
    | undefined;
  const agentAppSurface = surfaces.find((s) => s.type === "agentApp") as
    | {
        route?: string;
        systemPrompt?: string;
        tools?: string[];
        budget?: { max_iterations?: number; max_tool_calls?: number; max_tokens?: number };
      }
    | undefined;

  const objectAppActions = objectAppSurface
    ? (definition.actionRefs ?? [])
        .map((a) => a.action)
        .filter((name) => name.split(".", 2)[0] === objectAppSurface.objectType)
    : [];

  return {
    objectApp: objectAppSurface
      ? { enabled: true, objectType: objectAppSurface.objectType ?? "", route: objectAppSurface.route ?? "", actions: objectAppActions }
      : DEFAULT_STATE.objectApp,
    dashboard: dashboardSurface
      ? {
          enabled: true,
          route: dashboardSurface.route ?? "",
          widgets: (dashboardSurface.widgets ?? []).map((w) => ({
            id: crypto.randomUUID(),
            component: w.component === "kpi" ? "kpi" : "table",
            objectType: w.objectType ?? "",
            label: w.label ?? "",
          })),
        }
      : DEFAULT_STATE.dashboard,
    agentApp: agentAppSurface
      ? {
          enabled: true,
          route: agentAppSurface.route ?? "",
          systemPrompt: agentAppSurface.systemPrompt ?? "",
          tools: agentAppSurface.tools ?? [],
          maxIterations: agentAppSurface.budget?.max_iterations ?? DEFAULT_STATE.agentApp.maxIterations,
          maxToolCalls: agentAppSurface.budget?.max_tool_calls ?? DEFAULT_STATE.agentApp.maxToolCalls,
          maxTokens: agentAppSurface.budget?.max_tokens ?? DEFAULT_STATE.agentApp.maxTokens,
        }
      : DEFAULT_STATE.agentApp,
  };
}

function stateToDefinition(state: BuilderState, actions: ActionDefinition[]): ApplicationDefinition {
  const surfaces: Array<Record<string, unknown>> = [];
  const bindings: Array<Record<string, unknown>> = [];
  const actionRefs: Array<{ action: string; riskClass: string }> = [];

  if (state.objectApp.enabled && state.objectApp.objectType) {
    surfaces.push({ type: "objectApp", objectType: state.objectApp.objectType, route: state.objectApp.route || "/apps/app" });
    bindings.push(
      { component: "table", objectType: state.objectApp.objectType },
      { component: "detail", objectType: state.objectApp.objectType },
    );
    for (const actionName of state.objectApp.actions) {
      const riskLevel = actions.find((a) => a.name === actionName)?.risk_level ?? "low";
      actionRefs.push({ action: actionName, riskClass: riskLevel });
    }
  }

  if (state.dashboard.enabled) {
    surfaces.push({
      type: "dashboard",
      route: state.dashboard.route || "/apps/app/dashboard",
      widgets: state.dashboard.widgets
        .filter((w) => w.objectType)
        .map((w) => ({ component: w.component, objectType: w.objectType, label: w.label || w.objectType })),
    });
  }

  if (state.agentApp.enabled) {
    surfaces.push({
      type: "agentApp",
      route: state.agentApp.route || "/apps/app/agent",
      tools: state.agentApp.tools,
      systemPrompt: state.agentApp.systemPrompt,
      budget: {
        max_iterations: state.agentApp.maxIterations,
        max_tool_calls: state.agentApp.maxToolCalls,
        max_tokens: state.agentApp.maxTokens,
      },
    });
  }

  return { surfaces, bindings, actionRefs };
}

export function ApplicationBuilder({
  definition,
  objectTypes,
  actions,
  tools,
  onSave,
  saving,
  saveError,
}: {
  definition: ApplicationDefinition;
  objectTypes: ObjectType[];
  actions: ActionDefinition[];
  tools: ToolDefinition[];
  onSave: (definition: ApplicationDefinition) => void;
  saving: boolean;
  saveError: string | null;
}) {
  const [state, setState] = useState<BuilderState>(() => definitionToState(definition));

  useEffect(() => {
    setState(definitionToState(definition));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [definition]);

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over) return;
    const draggedKind = active.data.current?.kind as "kpi" | "table" | undefined;

    if (draggedKind) {
      // Dropped a palette chip — anywhere over the canvas or an existing
      // widget both count as "add it to the dashboard."
      const widgetIds = state.dashboard.widgets.map((w) => w.id);
      if (over.id === "dashboard-canvas" || widgetIds.includes(over.id as string)) {
        const newWidget: DashboardWidgetConfig = { id: crypto.randomUUID(), component: draggedKind, objectType: "", label: "" };
        setState((s) => ({ ...s, dashboard: { ...s.dashboard, enabled: true, widgets: [...s.dashboard.widgets, newWidget] } }));
      }
      return;
    }

    // Reordering an existing widget within the dashboard.
    const widgetIds = state.dashboard.widgets.map((w) => w.id);
    if (active.id !== over.id && widgetIds.includes(active.id as string) && widgetIds.includes(over.id as string)) {
      const oldIndex = widgetIds.indexOf(active.id as string);
      const newIndex = widgetIds.indexOf(over.id as string);
      setState((s) => ({ ...s, dashboard: { ...s.dashboard, widgets: arrayMove(s.dashboard.widgets, oldIndex, newIndex) } }));
    }
  }

  return (
    <DndContext onDragEnd={handleDragEnd}>
      <p style={{ fontSize: 12, color: "var(--hl-text-muted)", marginBottom: 12 }}>
        A visual editor over the same definition the Definition tab edits as raw JSON — enable the surfaces this
        application needs, drag widgets onto the dashboard canvas, and save. Switch to Definition for full manual
        control at any time.
      </p>
      {saveError && (
        <Callout intent="danger" style={{ marginBottom: 12 }}>
          {saveError}
        </Callout>
      )}
      <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>
        <div style={{ width: 180, flexShrink: 0, position: "sticky", top: 12 }}>
          <WidgetPalette />
        </div>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <ObjectAppSection
            value={state.objectApp}
            objectTypes={objectTypes}
            actions={actions}
            onChange={(objectApp) => setState((s) => ({ ...s, objectApp }))}
          />
          <DashboardSection
            value={state.dashboard}
            objectTypes={objectTypes}
            onChange={(dashboard) => setState((s) => ({ ...s, dashboard }))}
          />
          <AgentAppSection value={state.agentApp} tools={tools} onChange={(agentApp) => setState((s) => ({ ...s, agentApp }))} />
          <Button
            intent="primary"
            loading={saving}
            style={{ alignSelf: "flex-start" }}
            onClick={() => onSave(stateToDefinition(state, actions))}
          >
            Save draft
          </Button>
        </div>
      </div>
    </DndContext>
  );
}
