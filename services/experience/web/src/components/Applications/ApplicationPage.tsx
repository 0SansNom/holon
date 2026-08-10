import { Suspense, useEffect, useState } from "react";
import { useParams } from "@tanstack/react-router";
import { Button, Callout, HTMLSelect, Tab, Tabs, Tag } from "@blueprintjs/core";
import Editor from "@monaco-editor/react";
import { useMonacoEditorTheme } from "../../hooks/useMonacoEditorTheme";
import {
  useApplicationOptional,
  useApplicationDashboard,
  useObjectTypes,
  useActions,
  useTools,
  usePromoteApplication,
  useSaveApplication,
  useSetApplicationProject,
  useProjects,
} from "../../api/hooks";
import { DashboardWidgets } from "./DashboardWidgets";
import { ObjectAppView } from "./ObjectAppView";
import { ApplicationBuilder } from "./Builder/ApplicationBuilder";
import type { ApplicationDefinition } from "../../api/experience";
import { ApiError } from "../../api/client";
import { ResourceActionsMenu } from "../common/ResourceActionsMenu";
import { DetailPage } from "../common/PageLayout";
import { ObjectAppSkeleton } from "../common/Skeleton";

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
  const monacoTheme = useMonacoEditorTheme();
  const { data: application, error, refetch } = useApplicationOptional(name);
  const { data: dashboard } = useApplicationDashboard(application?.status === "promoted" ? name : undefined);
  const { data: objectTypes = [] } = useObjectTypes();
  const { data: actions = [] } = useActions();
  const { data: tools = [] } = useTools();
  const { data: projects = [] } = useProjects();
  const saveMutation = useSaveApplication(name);
  const promoteMutation = usePromoteApplication(name);
  const setProjectMutation = useSetApplicationProject(name);

  const [editorValue, setEditorValue] = useState(JSON.stringify(DEFAULT_DEFINITION, null, 2));
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveOk, setSaveOk] = useState(false);

  useEffect(() => {
    if (application) setEditorValue(JSON.stringify(application.definition, null, 2));
  }, [application]);

  const notFound = error instanceof ApiError && error.status === 404;

  // Shared by both editors — the Builder tab constructs a definition
  // object directly, the Definition tab parses one from Monaco's raw
  // JSON text; either way it's the same `POST /api/applications/{name}`
  // call and the same success/error handling.
  async function saveDefinition(definition: ApplicationDefinition) {
    setSaveError(null);
    setSaveOk(false);
    try {
      await saveMutation.mutateAsync(definition);
      setSaveOk(true);
      void refetch();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
    }
  }

  async function save() {
    try {
      await saveDefinition(JSON.parse(editorValue));
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
    <DetailPage
      breadcrumbs={[{ label: "Applications", to: "/applications" }, { label: name }]}
      title={name}
      actions={
        application ? (
          <div className="hl-flex-row hl-items-center hl-gap-sm">
            <Tag intent={application.status === "promoted" ? "success" : "warning"}>
              {application.status} · v{application.version}
            </Tag>
            <HTMLSelect
              minimal
              value={application.project_urn ?? ""}
              disabled={setProjectMutation.isPending}
              onChange={(e) => setProjectMutation.mutate(e.target.value || null)}
            >
              <option value="">No project</option>
              {projects.map((p) => (
                <option key={p.urn} value={p.urn}>
                  {p.name}
                </option>
              ))}
            </HTMLSelect>
            {application.status === "draft" && (
              <Button intent="primary" loading={promoteMutation.isPending} onClick={() => void promote()}>
                Promote
              </Button>
            )}
            <ResourceActionsMenu urn={application.urn} />
          </div>
        ) : undefined
      }
    >
      {notFound && <Callout intent="warning">No draft yet — edit the definition below and save to create one.</Callout>}
      {saveError && (
        <Callout intent="danger" className="hl-mt-sm">
          {saveError}
        </Callout>
      )}
      {saveOk && (
        <Callout intent="success" className="hl-mt-sm">
          Saved.
        </Callout>
      )}

      <div className="hl-mt-md">
        <Tabs id="application-tabs" renderActiveTabPanelOnly>
        <Tab
          id="builder"
          title="Builder"
          panel={
            <ApplicationBuilder
              definition={application?.definition ?? DEFAULT_DEFINITION}
              objectTypes={objectTypes}
              actions={actions}
              tools={tools}
              saving={saveMutation.isPending}
              saveError={saveError}
              onSave={(definition) => void saveDefinition(definition)}
            />
          }
        />
        <Tab
          id="app"
          title="App"
          panel={
            application ? (
              <Suspense fallback={<ObjectAppSkeleton />}>
                <ObjectAppView application={application} />
              </Suspense>
            ) : (
              <p className="hl-text-muted">Loading…</p>
            )
          }
        />
        <Tab
          id="dashboard"
          title="Dashboard"
          panel={
            application?.status === "promoted" && dashboard ? (
              <DashboardWidgets widgets={dashboard.widgets} />
            ) : (
              <p className="hl-text-muted">Promote the application to see its dashboard.</p>
            )
          }
        />
        <Tab
          id="definition"
          title="Definition"
          panel={
            <div>
              <p className="hl-ontology-tab-desc hl-mb-sm">
                Every binding/action/component is validated against Knowledge's real ontology on save.
              </p>
              <div className="hl-json-editor">
                <Editor
                  height="400px"
                  defaultLanguage="json"
                  theme={monacoTheme}
                  value={editorValue}
                  onChange={(v) => setEditorValue(v ?? "")}
                  options={{ minimap: { enabled: false }, fontSize: 13 }}
                />
              </div>
              <Button intent="primary" className="hl-mt-sm" loading={saveMutation.isPending} onClick={() => void save()}>
                Save draft
              </Button>
            </div>
          }
        />
      </Tabs>
      </div>
    </DetailPage>
  );
}
