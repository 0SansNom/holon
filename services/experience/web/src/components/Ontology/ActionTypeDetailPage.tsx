import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { Button, Callout, Tab, Tabs, Tag, type TabId } from "@blueprintjs/core";
import {
  useActionTypeObservability,
  useActionTypes,
  useInterfaces,
  useObjectTypes,
  useUpdateActionType,
} from "../../api/hooks";
import { DetailPage, PageSection } from "../common/PageLayout";
import { EmptyState, ErrorCallout } from "../common/ListPrimitives";
import { BranchesDialog } from "./BranchesDialog";
import { ActionTypeFormFields } from "./ActionTypeFormFields";
import {
  actionTypeFormFromRecord,
  parseActionTypeJsonFields,
  type ActionTypeFormState,
  DEFAULT_ACTION_TYPE_FORM,
} from "./actionTypeForm";
import { parseTypeClassesInput } from "./typeClassUtils";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useOntologyDiscoverStore } from "../../store/ontologyDiscover";
import { useApplications } from "../../api/hooks/useExperienceHooks";

type DetailTab = "overview" | "logic" | "observability";

export function ActionTypeDetailPage() {
  const { name: rawName } = useParams({ from: "/shell/ontology/action-types/$name" });
  const name = decodeURIComponent(rawName);
  const navigate = useNavigate();
  const { data: actionTypes = [] } = useActionTypes();
  const { data: objectTypes = [] } = useObjectTypes();
  const { data: interfaces = [] } = useInterfaces();
  const { data: applications = [] } = useApplications();
  const updateActionType = useUpdateActionType();
  const actionType = useMemo(() => actionTypes.find((a) => a.name === name), [actionTypes, name]);
  const { data: observability, isLoading: obsLoading, error: obsError } = useActionTypeObservability(
    name,
    30,
    !!actionType,
  );
  const applicationsUsing = useMemo(
    () =>
      applications
        .filter((app) => (app.dependencies?.actions ?? []).includes(name))
        .map((app) => ({ name: app.name })),
    [applications, name],
  );
  const recordVisit = useOntologyDiscoverStore((s) => s.recordVisit);
  const isFavorite = useOntologyDiscoverStore((s) => s.isFavorite("action_type", name));
  const toggleFavorite = useOntologyDiscoverStore((s) => s.toggleFavorite);

  const [tab, setTab] = useState<DetailTab>("overview");
  const [form, setForm] = useState<ActionTypeFormState>(DEFAULT_ACTION_TYPE_FORM);
  const [branching, setBranching] = useState(false);
  const [ok, setOk] = useState<string | null>(null);

  useEffect(() => {
    if (actionType) setForm(actionTypeFormFromRecord(actionType));
  }, [actionType]);

  useEffect(() => {
    if (actionType) recordVisit("action_type", actionType.name);
  }, [actionType, recordVisit]);

  const {
    submit: submitSave,
    error: saveError,
    isPending: savePending,
  } = useAsyncAction(async () => {
    if (!actionType) return;
    const parsed = parseActionTypeJsonFields(form);
    if (!parsed.ok) throw new Error(parsed.error);
    await updateActionType.mutateAsync({
      name: actionType.name,
      body: {
        name: actionType.name,
        target_object_type: actionType.target_object_type ?? undefined,
        target_interface: actionType.target_interface ?? undefined,
        required_permission: form.requiredPermission,
        risk_level: form.riskLevel as "low" | "high",
        description: form.description,
        parameters: parsed.parameters,
        edits: parsed.edits,
        submission_criteria: parsed.submission_criteria,
        function_side_effect: form.functionSideEffect?.trim() || undefined,
        writeback_dataset: form.writebackDataset?.trim() || undefined,
        notify_webhook: form.notifyWebhook?.trim() || undefined,
        edit_function: form.editsKind === "function" ? form.editFunctionName : undefined,
        sections: parsed.sections,
        type_classes: parseTypeClassesInput(form.typeClasses),
        lifecycle_status: form.lifecycleStatus,
        deprecation_reason: form.lifecycleStatus === "deprecated" ? form.deprecationReason : undefined,
        deprecation_deadline:
          form.lifecycleStatus === "deprecated" ? form.deprecationDeadline || undefined : undefined,
        replacement_urn: form.lifecycleStatus === "deprecated" ? form.replacementUrn || undefined : undefined,
      },
    });
    setOk("Saved.");
    setTab("overview");
  }, { successMessage: `"${name}" saved` });

  if (!actionType) {
    return (
      <DetailPage
        breadcrumbs={[
          { label: "Ontology", to: "/ontology", search: { tab: "action-types" } },
          { label: name },
        ]}
        title={name}
      >
        <EmptyState>Action Type not found.</EmptyState>
      </DetailPage>
    );
  }

  const localName = actionType.name.includes(".")
    ? actionType.name.split(".").slice(1).join(".")
    : actionType.name;

  return (
    <DetailPage
      breadcrumbs={[
        { label: "Ontology", to: "/ontology", search: { tab: "action-types" } },
        { label: actionType.name },
      ]}
      title={localName}
      description={
        <span className="hl-tag-row">
          <Tag minimal intent="primary">
            Action Type
          </Tag>
          {actionType.target_interface ? (
            <Tag minimal icon="layers">
              {actionType.target_interface}
            </Tag>
          ) : (
            <Tag minimal>{actionType.target_object_type}</Tag>
          )}
          <Tag minimal intent={actionType.risk_level === "high" ? "warning" : "none"}>
            {actionType.risk_level} risk
          </Tag>
          <Tag minimal>{actionType.lifecycle_status ?? "experimental"}</Tag>
        </span>
      }
      actions={
        <div className="hl-flex-row hl-gap-sm">
          <Button
            icon={isFavorite ? "star" : "star-empty"}
            intent={isFavorite ? "warning" : "none"}
            onClick={() => toggleFavorite("action_type", actionType.name)}
          >
            {isFavorite ? "Favorited" : "Favorite"}
          </Button>
          <Button icon="git-branch" onClick={() => setBranching(true)}>
            Branches
          </Button>
          <Button intent="primary" icon="floppy-disk" loading={savePending} onClick={() => void submitSave(undefined)}>
            Save
          </Button>
        </div>
      }
    >
      {(saveError || ok) && (
        <div className="hl-mb-md">
          {saveError && <ErrorCallout>{saveError}</ErrorCallout>}
          {ok && !saveError && <Callout intent="success">{ok}</Callout>}
        </div>
      )}

      <Tabs
        id="action-type-detail"
        selectedTabId={tab}
        onChange={(id: TabId) => setTab(String(id) as DetailTab)}
        renderActiveTabPanelOnly
        className="hl-ot-draft-tabs"
      >
        <Tab
          id="overview"
          title="Overview"
          panel={
            <PageSection title="Overview">
              <dl className="hl-ot-overview-meta">
                <div>
                  <dt>Full name</dt>
                  <dd className="hl-mono">{actionType.name}</dd>
                </div>
                <div>
                  <dt>Target</dt>
                  <dd>
                    {actionType.target_interface ? (
                      <>Interface {actionType.target_interface}</>
                    ) : actionType.target_object_type ? (
                      <Link
                        to="/ontology/object-types/$name"
                        params={{ name: actionType.target_object_type }}
                        className="hl-link-accent"
                      >
                        {actionType.target_object_type}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Permission</dt>
                  <dd className="hl-mono">{actionType.required_permission}</dd>
                </div>
                <div>
                  <dt>Parameters</dt>
                  <dd>{actionType.parameters?.length ?? 0}</dd>
                </div>
                <div>
                  <dt>Edits</dt>
                  <dd>
                    {actionType.edit_function
                      ? `function: ${actionType.edit_function}`
                      : `${actionType.edits?.length ?? 0} declarative`}
                  </dd>
                </div>
                <div>
                  <dt>Writeback</dt>
                  <dd className="hl-mono">{actionType.writeback_dataset || "—"}</dd>
                </div>
              </dl>
              {actionType.description && <p className="hl-mt-md">{actionType.description}</p>}
              <div className="hl-mt-md">
                <h5 className="hl-text-muted-sm">Applications ({applicationsUsing.length})</h5>
                {applicationsUsing.length === 0 ? (
                  <p className="hl-text-muted">None declared in application dependencies</p>
                ) : (
                  <ul className="hl-ot-overview-deps">
                    {applicationsUsing.map((app) => (
                      <li key={app.name}>
                        <Link to="/applications/$name" params={{ name: app.name }} className="hl-link-accent">
                          {app.name}
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <p className="hl-text-muted-sm hl-mt-md">
                Edit parameters, edits, and criteria on the{" "}
                <button type="button" className="hl-link-accent" onClick={() => setTab("logic")}>
                  Logic
                </button>{" "}
                tab.
              </p>
            </PageSection>
          }
        />
        <Tab
          id="logic"
          title="Logic"
          panel={
            <PageSection title="Logic">
              <ActionTypeFormFields
                mode="edit"
                value={form}
                onChange={(patch) => setForm((prev) => ({ ...prev, ...patch }))}
                objectTypes={objectTypes}
                interfaces={interfaces}
                fixedName={actionType.name}
              />
            </PageSection>
          }
        />
        <Tab
          id="observability"
          title="Observability"
          panel={
            <PageSection title="Observability (last 30 days)">
              {obsLoading && <p className="hl-text-muted">Loading invocation stats…</p>}
              {obsError && (
                <ErrorCallout>
                  Could not load observability — is Knowledge up to date with the observability endpoint?
                </ErrorCallout>
              )}
              {observability && (
                <>
                  <div className="hl-om-obs-stats">
                    <div className="hl-om-obs-stat">
                      <div className="hl-om-obs-stat-value">{observability.invocations}</div>
                      <div className="hl-text-muted-sm">Invocations</div>
                    </div>
                    <div className="hl-om-obs-stat">
                      <div className="hl-om-obs-stat-value">{observability.with_edits}</div>
                      <div className="hl-text-muted-sm">With edits</div>
                    </div>
                    <div className="hl-om-obs-stat">
                      <div className="hl-om-obs-stat-value">{observability.reverted}</div>
                      <div className="hl-text-muted-sm">Reverted</div>
                    </div>
                    <div className="hl-om-obs-stat">
                      <div className="hl-om-obs-stat-value">{observability.approvals.pending}</div>
                      <div className="hl-text-muted-sm">Approvals pending</div>
                    </div>
                    <div className="hl-om-obs-stat">
                      <div className="hl-om-obs-stat-value">{observability.approvals.approved}</div>
                      <div className="hl-text-muted-sm">Approved</div>
                    </div>
                    <div className="hl-om-obs-stat">
                      <div className="hl-om-obs-stat-value">{observability.approvals.rejected}</div>
                      <div className="hl-text-muted-sm">Rejected</div>
                    </div>
                  </div>
                  {observability.by_day.length === 0 ? (
                    <p className="hl-text-muted hl-mt-md">No invocations in this window.</p>
                  ) : (
                    <table className="hl-data-table hl-data-table-compact hl-mt-md">
                      <thead>
                        <tr>
                          <th>Day</th>
                          <th>Invocations</th>
                        </tr>
                      </thead>
                      <tbody>
                        {observability.by_day.map((row) => (
                          <tr key={row.day}>
                            <td className="hl-mono">{row.day}</td>
                            <td>{row.invocations}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  <Callout className="hl-mt-md" icon="info-sign">
                    Counts come from <code>action_invocation</code> / <code>action_approval</code>. Monitoring rules
                    (Foundry-style alerts) are not configured in Holon yet.
                  </Callout>
                </>
              )}
            </PageSection>
          }
        />
      </Tabs>

      {branching && (
        <BranchesDialog
          kind="action_type"
          resourceName={actionType.name}
          currentDefinition={{
            target_object_type: actionType.target_object_type,
            target_interface: actionType.target_interface,
            required_permission: actionType.required_permission,
            risk_level: actionType.risk_level,
            description: actionType.description,
            parameters: actionType.parameters,
            edits: actionType.edits,
            submission_criteria: actionType.submission_criteria,
            function_side_effect: actionType.function_side_effect,
            writeback_dataset: actionType.writeback_dataset,
            edit_function: actionType.edit_function,
            sections: actionType.sections,
            type_classes: actionType.type_classes ?? [],
            lifecycle_status: actionType.lifecycle_status,
            deprecation_reason: actionType.deprecation_reason,
            deprecation_deadline: actionType.deprecation_deadline,
            replacement_urn: actionType.replacement_urn,
          }}
          onClose={() => setBranching(false)}
        />
      )}

      <div className="hl-ot-draft-footer">
        <Button minimal icon="arrow-left" onClick={() => void navigate({ to: "/ontology", search: { tab: "action-types" } })}>
          Back to Action Types
        </Button>
        <Button intent="primary" icon="floppy-disk" loading={savePending} onClick={() => void submitSave(undefined)}>
          Save
        </Button>
      </div>
    </DetailPage>
  );
}
