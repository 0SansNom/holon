import { useRef, useState } from "react";
import { DndContext, type DragEndEvent } from "@dnd-kit/core";
import { arrayMove } from "@dnd-kit/sortable";
import { Button, Callout } from "@blueprintjs/core";
import type { ApplicationDefinition, ApplicationSurface } from "../../../api/experience";
import { isAgentAppSurface, isDashboardSurface, isObjectAppSurface } from "../../../api/experience";
import type { ObjectSet, ObjectType, ActionDefinition, RelationType } from "../../../api/knowledge";
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
  objectApp: { enabled: false, objectType: "", objectSet: "", route: "", actions: [], links: [] },
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

function definitionToState(definition: ApplicationDefinition): BuilderState {
  const surfaces = definition.surfaces ?? [];
  const objectAppSurface = surfaces.find(isObjectAppSurface);
  const dashboardSurface = surfaces.find(isDashboardSurface);
  const agentAppSurface = surfaces.find(isAgentAppSurface);

  const objectAppActions = objectAppSurface
    ? (definition.actionRefs ?? [])
        .map((a) => a.action)
        .filter((name) => name.split(".", 2)[0] === objectAppSurface.objectType)
    : [];

  return {
    objectApp: objectAppSurface
      ? {
          enabled: true,
          objectType: objectAppSurface.objectType ?? "",
          objectSet: objectAppSurface.objectSet ?? "",
          route: objectAppSurface.route ?? "",
          actions: objectAppActions,
          links: objectAppSurface.links ?? [],
        }
      : DEFAULT_STATE.objectApp,
    dashboard: dashboardSurface
      ? {
          enabled: true,
          route: dashboardSurface.route ?? "",
          widgets: (dashboardSurface.widgets ?? []).map((w) => ({
            id: crypto.randomUUID(),
            component: w.component === "kpi" ? "kpi" : "table",
            objectType: w.objectType ?? "",
            objectSet: w.objectSet ?? "",
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

function stateToDefinition(state: BuilderState, actions: ActionDefinition[], existingSurfaces: ApplicationSurface[]): ApplicationDefinition {
  const preservedSurfaces = existingSurfaces.filter(
    (s) => !isObjectAppSurface(s) && !isDashboardSurface(s) && !isAgentAppSurface(s),
  );
  const surfaces: ApplicationSurface[] = [...preservedSurfaces];
  const bindings: ApplicationDefinition["bindings"] = [];
  const actionRefs: ApplicationDefinition["actionRefs"] = [];

  if (state.objectApp.enabled && state.objectApp.objectType) {
    surfaces.push({
      type: "objectApp",
      objectType: state.objectApp.objectType,
      ...(state.objectApp.objectSet ? { objectSet: state.objectApp.objectSet } : {}),
      ...(state.objectApp.links.length > 0 ? { links: state.objectApp.links } : {}),
      route: state.objectApp.route || "/apps/app",
    });
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
        .map((w) => ({
          component: w.component,
          objectType: w.objectType,
          ...(w.objectSet ? { objectSet: w.objectSet } : {}),
          label: w.label || w.objectSet || w.objectType,
        })),
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
  objectSets,
  actions,
  relationTypes,
  tools,
  onSave,
  saving,
  saveError,
}: {
  definition: ApplicationDefinition;
  objectTypes: ObjectType[];
  objectSets: ObjectSet[];
  actions: ActionDefinition[];
  relationTypes: RelationType[];
  tools: ToolDefinition[];
  onSave: (definition: ApplicationDefinition) => void;
  saving: boolean;
  saveError: string | null;
}) {
  const definitionKey = JSON.stringify(definition);
  const prevDefinitionKey = useRef(definitionKey);
  const [state, setState] = useState<BuilderState>(() => definitionToState(definition));

  if (prevDefinitionKey.current !== definitionKey) {
    prevDefinitionKey.current = definitionKey;
    setState(definitionToState(definition));
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over) return;
    const draggedKind = active.data.current?.kind as "kpi" | "table" | undefined;

    if (draggedKind) {
      const widgetIds = state.dashboard.widgets.map((w) => w.id);
      if (over.id === "dashboard-canvas" || widgetIds.includes(over.id as string)) {
        const newWidget: DashboardWidgetConfig = {
          id: crypto.randomUUID(),
          component: draggedKind,
          objectType: "",
          objectSet: "",
          label: "",
        };
        setState((s) => ({ ...s, dashboard: { ...s.dashboard, enabled: true, widgets: [...s.dashboard.widgets, newWidget] } }));
      }
      return;
    }

    const widgetIds = state.dashboard.widgets.map((w) => w.id);
    if (active.id !== over.id && widgetIds.includes(active.id as string) && widgetIds.includes(over.id as string)) {
      const oldIndex = widgetIds.indexOf(active.id as string);
      const newIndex = widgetIds.indexOf(over.id as string);
      setState((s) => ({ ...s, dashboard: { ...s.dashboard, widgets: arrayMove(s.dashboard.widgets, oldIndex, newIndex) } }));
    }
  }

  return (
    <DndContext onDragEnd={handleDragEnd}>
      <p className="hl-ontology-tab-desc hl-mb-sm">
        A visual editor over the same definition the Definition tab edits as raw JSON — enable the surfaces this
        application needs, drag widgets onto the dashboard canvas, bind Object Sets for filtered views, and save.
      </p>
      {saveError && (
        <Callout intent="danger" className="hl-mb-sm">
          {saveError}
        </Callout>
      )}
      <div className="hl-flex-row hl-gap-lg hl-items-start">
        <div className="hl-builder-palette">
          <WidgetPalette />
        </div>
        <div className="hl-flex-col hl-gap-md hl-flex-1 hl-min-w-0">
          <ObjectAppSection
            value={state.objectApp}
            objectTypes={objectTypes}
            objectSets={objectSets}
            actions={actions}
            relationTypes={relationTypes}
            onChange={(objectApp) => setState((s) => ({ ...s, objectApp }))}
          />
          <DashboardSection
            value={state.dashboard}
            objectTypes={objectTypes}
            objectSets={objectSets}
            onChange={(dashboard) => setState((s) => ({ ...s, dashboard }))}
          />
          <AgentAppSection value={state.agentApp} tools={tools} onChange={(agentApp) => setState((s) => ({ ...s, agentApp }))} />
          <Button
            intent="primary"
            loading={saving}
            className="hl-self-start"
            onClick={() => onSave(stateToDefinition(state, actions, definition.surfaces ?? []))}
          >
            Save draft
          </Button>
        </div>
      </div>
    </DndContext>
  );
}
