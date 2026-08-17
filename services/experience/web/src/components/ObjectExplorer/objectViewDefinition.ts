/** Client-side Configured Object View definition (Foundry OV config parity, local). */

export type ObjectViewWidgetKind =
  | "overview"
  | "properties"
  | "links"
  | "media"
  | "timeline"
  | "graph"
  | "property_kpi"
  | "iframe"
  | "markdown";

export type ObjectViewWidget = {
  id: string;
  kind: ObjectViewWidgetKind;
  label?: string;
  /** property_kpi: source property key on the object */
  propertyKey?: string;
  /** iframe: URL; supports {{objectType}} and {{objectId}} */
  iframeUrl?: string;
  /** markdown: static note body */
  markdown?: string;
};

export type ObjectViewTabDef = {
  id: string;
  title: string;
  widgets: ObjectViewWidget[];
};

export type ObjectViewDefinition = {
  objectType: string;
  version: 1;
  tabs: ObjectViewTabDef[];
  updatedAt: number;
};

export type ObjectViewMode = "standard" | "configured";

const WIDGET_KINDS = new Set<ObjectViewWidgetKind>([
  "overview",
  "properties",
  "links",
  "media",
  "timeline",
  "graph",
  "property_kpi",
  "iframe",
  "markdown",
]);

function newId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

export function defaultObjectViewDefinition(objectType: string): ObjectViewDefinition {
  return {
    objectType,
    version: 1,
    tabs: [
      {
        id: "main",
        title: "Main",
        widgets: [
          { id: newId("w"), kind: "overview" },
          { id: newId("w"), kind: "links" },
        ],
      },
      {
        id: "details",
        title: "Details",
        widgets: [
          { id: newId("w"), kind: "properties" },
          { id: newId("w"), kind: "media" },
        ],
      },
      {
        id: "history",
        title: "History",
        widgets: [{ id: newId("w"), kind: "timeline" }],
      },
    ],
    updatedAt: Date.now(),
  };
}

export function normalizeWidget(raw: Partial<ObjectViewWidget> | null | undefined): ObjectViewWidget | null {
  if (!raw || typeof raw !== "object") return null;
  const kind = raw.kind;
  if (typeof kind !== "string" || !WIDGET_KINDS.has(kind as ObjectViewWidgetKind)) return null;
  const id = typeof raw.id === "string" && raw.id.trim() ? raw.id.trim() : newId("w");
  const widget: ObjectViewWidget = { id, kind: kind as ObjectViewWidgetKind };
  if (typeof raw.label === "string" && raw.label.trim()) widget.label = raw.label.trim();
  if (typeof raw.propertyKey === "string" && raw.propertyKey.trim()) widget.propertyKey = raw.propertyKey.trim();
  if (typeof raw.iframeUrl === "string" && raw.iframeUrl.trim()) widget.iframeUrl = raw.iframeUrl.trim();
  if (typeof raw.markdown === "string") widget.markdown = raw.markdown;
  return widget;
}

export function normalizeTab(raw: Partial<ObjectViewTabDef> | null | undefined): ObjectViewTabDef | null {
  if (!raw || typeof raw !== "object") return null;
  const id = typeof raw.id === "string" && raw.id.trim() ? raw.id.trim() : newId("tab");
  const title = typeof raw.title === "string" && raw.title.trim() ? raw.title.trim() : id;
  const widgets = Array.isArray(raw.widgets)
    ? raw.widgets.map((w) => normalizeWidget(w as Partial<ObjectViewWidget>)).filter((w): w is ObjectViewWidget => w != null)
    : [];
  return { id, title, widgets };
}

export function normalizeObjectViewDefinition(
  raw: Partial<ObjectViewDefinition> | null | undefined,
  fallbackObjectType?: string,
): ObjectViewDefinition | null {
  if (!raw || typeof raw !== "object") return null;
  const objectType =
    (typeof raw.objectType === "string" && raw.objectType.trim()) ||
    (typeof fallbackObjectType === "string" && fallbackObjectType.trim()) ||
    "";
  if (!objectType) return null;
  const tabs = Array.isArray(raw.tabs)
    ? raw.tabs.map((t) => normalizeTab(t as Partial<ObjectViewTabDef>)).filter((t): t is ObjectViewTabDef => t != null)
    : [];
  if (tabs.length === 0) return null;
  return {
    objectType,
    version: 1,
    tabs,
    updatedAt: typeof raw.updatedAt === "number" && Number.isFinite(raw.updatedAt) ? raw.updatedAt : Date.now(),
  };
}

export function resolveIframeUrl(template: string, objectType: string, objectId: string): string {
  return template.replaceAll("{{objectType}}", objectType).replaceAll("{{objectId}}", objectId);
}

export function widgetKindLabel(kind: ObjectViewWidgetKind): string {
  switch (kind) {
    case "overview":
      return "Overview";
    case "properties":
      return "Properties";
    case "links":
      return "Links";
    case "media":
      return "Media";
    case "timeline":
      return "Timeline";
    case "graph":
      return "Graph";
    case "property_kpi":
      return "Property KPI";
    case "iframe":
      return "Iframe";
    case "markdown":
      return "Note";
    default:
      return kind;
  }
}
