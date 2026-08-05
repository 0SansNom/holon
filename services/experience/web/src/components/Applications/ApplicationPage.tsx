import { useEffect, useState } from "react";
import { useParams } from "@tanstack/react-router";
import { Button, Callout, H3, Tab, Tabs, Tag } from "@blueprintjs/core";
import Editor from "@monaco-editor/react";
import { useApplication, useApplicationDashboard, usePromoteApplication, useSaveApplication } from "../../api/hooks";
import { DashboardWidgets } from "./DashboardWidgets";
import { ApiError } from "../../api/client";

const DEFAULT_DEFINITION = {
  surfaces: [{ type: "objectApp", objectType: "Customer", route: "/apps/example" }],
  bindings: [
    { component: "table", objectType: "Customer" },
    { component: "detail", objectType: "Customer" },
  ],
  actionRefs: [{ action: "Customer.putOnCreditHold", riskClass: "low" }],
};

export function ApplicationPage() {
  const { name } = useParams({ from: "/shell/applications/$name" });
  const { data: application, error, refetch } = useApplication(name);
  const { data: dashboard } = useApplicationDashboard(application?.status === "promoted" ? name : undefined);
  const saveMutation = useSaveApplication(name);
  const promoteMutation = usePromoteApplication(name);

  const [editorValue, setEditorValue] = useState(JSON.stringify(DEFAULT_DEFINITION, null, 2));
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveOk, setSaveOk] = useState(false);

  useEffect(() => {
    if (application) setEditorValue(JSON.stringify(application.definition, null, 2));
  }, [application]);

  const notFound = error instanceof ApiError && error.status === 404;

  async function save() {
    setSaveError(null);
    setSaveOk(false);
    try {
      const definition = JSON.parse(editorValue);
      await saveMutation.mutateAsync(definition);
      setSaveOk(true);
      void refetch();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
    }
  }

  async function promote() {
    setSaveError(null);
    try {
      await promoteMutation.mutateAsync();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Promote failed");
    }
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <H3>{name}</H3>
        {application && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Tag intent={application.status === "promoted" ? "success" : "warning"}>
              {application.status} · v{application.version}
            </Tag>
            {application.status === "draft" && (
              <Button intent="primary" loading={promoteMutation.isPending} onClick={() => void promote()}>
                Promote
              </Button>
            )}
          </div>
        )}
      </div>

      {notFound && <Callout intent="warning">No draft yet — edit the definition below and save to create one.</Callout>}
      {saveError && (
        <Callout intent="danger" style={{ marginTop: 12 }}>
          {saveError}
        </Callout>
      )}
      {saveOk && (
        <Callout intent="success" style={{ marginTop: 12 }}>
          Saved.
        </Callout>
      )}

      <div style={{ marginTop: 20 }}>
      <Tabs id="application-tabs" renderActiveTabPanelOnly>
        <Tab
          id="dashboard"
          title="Dashboard"
          panel={
            application?.status === "promoted" && dashboard ? (
              <DashboardWidgets widgets={dashboard.widgets} />
            ) : (
              <p style={{ color: "var(--hl-text-muted)" }}>Promote the application to see its dashboard.</p>
            )
          }
        />
        <Tab
          id="definition"
          title="Definition"
          panel={
            <div>
              <p style={{ fontSize: 12, color: "var(--hl-text-muted)", marginBottom: 8 }}>
                Every binding/action/component is validated against Knowledge's real ontology on save.
              </p>
              <div className="hl-panel" style={{ padding: 0, overflow: "hidden" }}>
                <Editor
                  height="400px"
                  defaultLanguage="json"
                  theme="vs-dark"
                  value={editorValue}
                  onChange={(v) => setEditorValue(v ?? "")}
                  options={{ minimap: { enabled: false }, fontSize: 13 }}
                />
              </div>
              <Button intent="primary" style={{ marginTop: 12 }} loading={saveMutation.isPending} onClick={() => void save()}>
                Save draft
              </Button>
            </div>
          }
        />
      </Tabs>
      </div>
    </div>
  );
}
