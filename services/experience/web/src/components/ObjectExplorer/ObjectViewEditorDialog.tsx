import { useEffect, useState } from "react";
import {
  Button,
  Classes,
  Dialog,
  DialogBody,
  DialogFooter,
  FormGroup,
  HTMLSelect,
  InputGroup,
  TextArea,
} from "@blueprintjs/core";
import {
  defaultObjectViewDefinition,
  normalizeObjectViewDefinition,
  widgetKindLabel,
  type ObjectViewDefinition,
  type ObjectViewTabDef,
  type ObjectViewWidget,
  type ObjectViewWidgetKind,
} from "./objectViewDefinition";
import { OBJECT_METADATA_KEYS } from "./objectExplorerUtils";

const ADDABLE_KINDS: ObjectViewWidgetKind[] = [
  "overview",
  "properties",
  "links",
  "media",
  "timeline",
  "graph",
  "property_kpi",
  "iframe",
  "markdown",
];

function newId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

export function ObjectViewEditorDialog({
  isOpen,
  objectType,
  objectKeys,
  initial,
  onClose,
  onSave,
  onDelete,
}: {
  isOpen: boolean;
  objectType: string;
  objectKeys: string[];
  initial?: ObjectViewDefinition | null;
  onClose: () => void;
  onSave: (definition: ObjectViewDefinition) => void;
  onDelete: () => void;
}) {
  const [definition, setDefinition] = useState<ObjectViewDefinition>(() =>
    normalizeObjectViewDefinition(initial ?? defaultObjectViewDefinition(objectType), objectType) ??
    defaultObjectViewDefinition(objectType),
  );
  const [selectedTabId, setSelectedTabId] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    const next =
      normalizeObjectViewDefinition(initial ?? defaultObjectViewDefinition(objectType), objectType) ??
      defaultObjectViewDefinition(objectType);
    setDefinition(next);
    setSelectedTabId(next.tabs[0]?.id ?? null);
  }, [isOpen, objectType, initial]);

  const activeTabId = selectedTabId ?? definition.tabs[0]?.id ?? null;
  const activeTab = definition.tabs.find((t) => t.id === activeTabId) ?? definition.tabs[0];
  const propertyOptions = objectKeys.filter((k) => !OBJECT_METADATA_KEYS.has(k));

  function updateTabs(tabs: ObjectViewTabDef[]) {
    setDefinition({ ...definition, tabs, updatedAt: Date.now() });
  }

  function updateActiveTab(patch: Partial<ObjectViewTabDef>) {
    if (!activeTab) return;
    updateTabs(definition.tabs.map((t) => (t.id === activeTab.id ? { ...t, ...patch } : t)));
  }

  function addTab() {
    const id = newId("tab");
    const tab: ObjectViewTabDef = { id, title: "New tab", widgets: [] };
    updateTabs([...definition.tabs, tab]);
    setSelectedTabId(id);
  }

  function removeTab(id: string) {
    if (definition.tabs.length <= 1) return;
    const next = definition.tabs.filter((t) => t.id !== id);
    updateTabs(next);
    setSelectedTabId(next[0]?.id ?? null);
  }

  function addWidget(kind: ObjectViewWidgetKind) {
    if (!activeTab) return;
    const widget: ObjectViewWidget = { id: newId("w"), kind };
    if (kind === "property_kpi" && propertyOptions[0]) widget.propertyKey = propertyOptions[0];
    if (kind === "markdown") widget.markdown = "";
    updateActiveTab({ widgets: [...activeTab.widgets, widget] });
  }

  function updateWidget(widgetId: string, patch: Partial<ObjectViewWidget>) {
    if (!activeTab) return;
    updateActiveTab({
      widgets: activeTab.widgets.map((w) => (w.id === widgetId ? { ...w, ...patch } : w)),
    });
  }

  function moveWidget(widgetId: string, dir: -1 | 1) {
    if (!activeTab) return;
    const idx = activeTab.widgets.findIndex((w) => w.id === widgetId);
    if (idx < 0) return;
    const nextIdx = idx + dir;
    if (nextIdx < 0 || nextIdx >= activeTab.widgets.length) return;
    const widgets = [...activeTab.widgets];
    const [item] = widgets.splice(idx, 1);
    widgets.splice(nextIdx, 0, item);
    updateActiveTab({ widgets });
  }

  function removeWidget(widgetId: string) {
    if (!activeTab) return;
    updateActiveTab({ widgets: activeTab.widgets.filter((w) => w.id !== widgetId) });
  }

  function handleSave() {
    const normalized = normalizeObjectViewDefinition(definition, objectType);
    if (!normalized) return;
    onSave(normalized);
    onClose();
  }

  function handleResetDefault() {
    const fresh = defaultObjectViewDefinition(objectType);
    setDefinition(fresh);
    setSelectedTabId(fresh.tabs[0]?.id ?? null);
  }

  function handleClose() {
    onClose();
  }

  return (
    <Dialog
      isOpen={isOpen}
      onClose={handleClose}
      title={`Edit Object View — ${objectType}`}
      className="hl-oe-ov-editor-dialog"
    >
      <DialogBody>
        <p className="hl-text-muted-sm hl-mb-md">
          Configure tabs and widgets for this ObjectType. Saved locally (browser). When present, Configured
          becomes the default view with a Standard toggle.
        </p>

        <div className="hl-oe-ov-editor-layout">
          <div className="hl-oe-ov-editor-tabs">
            <div className="hl-flex-between hl-items-center hl-mb-sm">
              <strong>Tabs</strong>
              <Button small icon="plus" onClick={addTab}>
                Add
              </Button>
            </div>
            <ul className="hl-oe-ov-editor-tab-list">
              {definition.tabs.map((tab) => (
                <li key={tab.id}>
                  <button
                    type="button"
                    className={
                      tab.id === activeTab?.id
                        ? "hl-oe-ov-editor-tab-btn hl-oe-ov-editor-tab-btn--active"
                        : "hl-oe-ov-editor-tab-btn"
                    }
                    onClick={() => setSelectedTabId(tab.id)}
                  >
                    {tab.title}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="hl-oe-ov-editor-main">
            {activeTab ? (
              <>
                <FormGroup label="Tab title" labelFor="ov-tab-title">
                  <InputGroup
                    id="ov-tab-title"
                    value={activeTab.title}
                    onChange={(e) => updateActiveTab({ title: e.target.value })}
                  />
                </FormGroup>
                <div className="hl-flex-row hl-gap-sm hl-mb-md">
                  <Button
                    small
                    intent="danger"
                    minimal
                    icon="trash"
                    disabled={definition.tabs.length <= 1}
                    onClick={() => removeTab(activeTab.id)}
                  >
                    Remove tab
                  </Button>
                </div>

                <div className="hl-flex-between hl-items-center hl-mb-sm">
                  <strong>Widgets</strong>
                  <HTMLSelect
                    value=""
                    onChange={(e) => {
                      const kind = e.target.value as ObjectViewWidgetKind;
                      if (kind) addWidget(kind);
                      e.target.value = "";
                    }}
                  >
                    <option value="">Add widget…</option>
                    {ADDABLE_KINDS.map((kind) => (
                      <option key={kind} value={kind}>
                        {widgetKindLabel(kind)}
                      </option>
                    ))}
                  </HTMLSelect>
                </div>

                {activeTab.widgets.length === 0 ? (
                  <p className="hl-text-muted">No widgets yet.</p>
                ) : (
                  <ul className="hl-oe-ov-editor-widgets">
                    {activeTab.widgets.map((widget, index) => (
                      <li key={widget.id} className="hl-oe-ov-editor-widget">
                        <div className="hl-flex-between hl-items-center">
                          <strong>{widgetKindLabel(widget.kind)}</strong>
                          <div className="hl-flex-row hl-gap-xs">
                            <Button
                              minimal
                              small
                              icon="arrow-up"
                              disabled={index === 0}
                              onClick={() => moveWidget(widget.id, -1)}
                            />
                            <Button
                              minimal
                              small
                              icon="arrow-down"
                              disabled={index === activeTab.widgets.length - 1}
                              onClick={() => moveWidget(widget.id, 1)}
                            />
                            <Button
                              minimal
                              small
                              icon="trash"
                              intent="danger"
                              onClick={() => removeWidget(widget.id)}
                            />
                          </div>
                        </div>
                        <FormGroup label="Label (optional)" className="hl-mt-sm">
                          <InputGroup
                            value={widget.label ?? ""}
                            placeholder={widgetKindLabel(widget.kind)}
                            onChange={(e) =>
                              updateWidget(widget.id, { label: e.target.value || undefined })
                            }
                          />
                        </FormGroup>
                        {widget.kind === "property_kpi" && (
                          <FormGroup label="Property">
                            <HTMLSelect
                              fill
                              value={widget.propertyKey ?? ""}
                              onChange={(e) => updateWidget(widget.id, { propertyKey: e.target.value })}
                            >
                              <option value="">Select…</option>
                              {propertyOptions.map((key) => (
                                <option key={key} value={key}>
                                  {key}
                                </option>
                              ))}
                            </HTMLSelect>
                          </FormGroup>
                        )}
                        {widget.kind === "iframe" && (
                          <FormGroup
                            label="URL"
                            helperText="Supports {{objectType}} and {{objectId}}"
                          >
                            <InputGroup
                              value={widget.iframeUrl ?? ""}
                              placeholder="https://…"
                              onChange={(e) => updateWidget(widget.id, { iframeUrl: e.target.value })}
                            />
                          </FormGroup>
                        )}
                        {widget.kind === "markdown" && (
                          <FormGroup label="Note">
                            <TextArea
                              fill
                              rows={3}
                              value={widget.markdown ?? ""}
                              onChange={(e) => updateWidget(widget.id, { markdown: e.target.value })}
                            />
                          </FormGroup>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <p className="hl-text-muted">No tab selected.</p>
            )}
          </div>
        </div>
      </DialogBody>
      <DialogFooter
        actions={
          <>
            <Button className={Classes.MINIMAL} onClick={handleClose}>
              Cancel
            </Button>
            <Button intent="danger" minimal onClick={onDelete}>
              Delete configured view
            </Button>
            <Button onClick={handleResetDefault}>Reset default</Button>
            <Button intent="primary" onClick={handleSave}>
              Save
            </Button>
          </>
        }
      />
    </Dialog>
  );
}
