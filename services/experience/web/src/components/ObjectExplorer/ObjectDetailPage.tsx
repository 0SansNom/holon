import { useMemo, useState } from "react";
import { useParams, useNavigate, useSearch, Link } from "@tanstack/react-router";
import { Button, ButtonGroup, Callout, Tab, Tabs, Tag } from "@blueprintjs/core";
import {
  useActions,
  useObject,
  useObjectType,
  useInvokeAction,
  useRelationTypes,
  usePrincipals,
  useObjectTimeline,
  useRevertActionInvocation,
  useSharedPropertyTypes,
  useObjectLinks,
  useValueTypes,
} from "../../api/hooks";
import { getErrorMessage } from "../../api/client";
import { camelToSnake } from "../common/propertyFormatUtils";
import { buildFormatsBySourceKey } from "../Ontology/propertyEditorUtils";
import { findPropertyWithTypeClass, hasTypeClass } from "../Ontology/typeClassUtils";
import { DetailPage } from "../common/PageLayout";
import type { ActionDefinition, ConditionalFormatRule, RelationType } from "../../api/knowledge";
import { TENANT_ID, WORKSPACE_ID } from "../../api/config";
import {
  titleOf,
  urnShortName,
  buildRelatedLinksForObjectType,
  computeInlineEditableActions,
} from "./objectExplorerUtils";
import { RelatedLinkPanel } from "./RelatedLinkPanel";
import { ObjectPropertiesTable } from "./ObjectPropertiesTable";
import { ObjectActionsBar } from "./ObjectActionsBar";
import { ObjectTimelinePanel } from "./ObjectTimelinePanel";
import { ObjectViewOverview } from "./ObjectViewOverview";
import { ObjectMediaGallery } from "./ObjectMediaGallery";
import { collectObjectMediaItems } from "./objectMedia";
import { ActionInvokeDialog } from "./ActionInvokeDialog";
import { ObjectViewEditorDialog } from "./ObjectViewEditorDialog";
import { ObjectViewWidgetHost, type ObjectViewHostContext } from "./ObjectViewWidgetHost";
import {
  normalizeObjectViewDefinition,
  type ObjectViewMode,
} from "./objectViewDefinition";
import { useObjectViewDefinitionsStore } from "../../store/objectViewDefinitions";
import { useAuthStore } from "../../store/auth";
import { prefillActionParameters } from "./actionParameterPrefill";

type ObjectViewTab = string;

function HierarchyParentCrumb({
  type,
  id,
  relation,
}: {
  type: string;
  id: string;
  relation: RelationType;
}) {
  const localName = relation.name.includes(".") ? relation.name.split(".").slice(1).join(".") : relation.name;
  const linkName = (relation.source_api_name || localName).trim() || localName;
  const parentType = urnShortName(relation.target_object_type_urn);
  const { data } = useObjectLinks(type, id, linkName, true);
  const parent = data?.data?.[0];
  if (!parent) return null;
  const parentId = String(parent.id);
  const parentLabel = (parent.title as string | undefined) ?? (parent.name as string | undefined) ?? parentId;
  return (
    <Link to="/objects/$type/$id" params={{ type: parentType, id: parentId }} className="hl-link-accent">
      {parentLabel}
    </Link>
  );
}

export function ObjectDetailPage() {
  const { type, id } = useParams({ from: "/shell/objects/$type/$id" });
  const { tab: tabSearch, view: viewSearch } = useSearch({ from: "/shell/objects/$type/$id" });
  const navigate = useNavigate();
  const principalUrn = useAuthStore((s) => s.session?.principal.urn);
  const { data: object } = useObject(type, id);
  const { data: objectType } = useObjectType(type);
  const displayTitle = titleOf(object as Record<string, unknown> | undefined, objectType);
  const { data: actions } = useActions();
  const { data: relationTypes } = useRelationTypes();
  const { data: principals } = usePrincipals();
  const { data: sharedPropertyTypes = [] } = useSharedPropertyTypes();
  const { data: valueTypes = [] } = useValueTypes();
  const { data: timeline } = useObjectTimeline(type, id);
  const invokeAction = useInvokeAction(type);
  const revertInvocation = useRevertActionInvocation(type, id);

  const rawConfigured = useObjectViewDefinitionsStore((s) => s.byType[type]);
  const configuredDefinition = useMemo(
    () =>
      rawConfigured
        ? (normalizeObjectViewDefinition(rawConfigured, type) ?? undefined)
        : undefined,
    [rawConfigured, type],
  );
  const preferredMode = useObjectViewDefinitionsStore((s) => s.preferredModeByType[type]);
  const upsertDefinition = useObjectViewDefinitionsStore((s) => s.upsertDefinition);
  const ensureDefault = useObjectViewDefinitionsStore((s) => s.ensureDefault);
  const deleteDefinition = useObjectViewDefinitionsStore((s) => s.deleteDefinition);
  const setPreferredMode = useObjectViewDefinitionsStore((s) => s.setPreferredMode);

  const [activeActionName, setActiveActionName] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [actionParameters, setActionParameters] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);

  const viewMode: ObjectViewMode = useMemo(() => {
    if (viewSearch === "standard" || viewSearch === "configured") return viewSearch;
    if (preferredMode) return preferredMode;
    return configuredDefinition ? "configured" : "standard";
  }, [viewSearch, preferredMode, configuredDefinition]);

  const selectedTab =
    (tabSearch as ObjectViewTab | undefined) ??
    (viewMode === "configured" ? configuredDefinition?.tabs[0]?.id ?? "main" : "overview");

  const relevantActions = (actions ?? []).filter(
    (a) => a.target_object_type === type || (a.target_interface && (objectType?.implements ?? []).includes(a.target_interface)),
  );
  const activeAction = relevantActions.find((a) => a.name === activeActionName);
  const maskedFields = (object?._maskedFields as string[] | undefined) ?? [];

  const iconProperty = findPropertyWithTypeClass(objectType?.property_types, "hubble", "icon");
  const iconUrl =
    iconProperty && object
      ? String(
          object[camelToSnake(iconProperty)] ??
            object[iconProperty] ??
            object[objectType?.property_mapping?.[iconProperty] ?? ""] ??
            "",
        )
      : "";

  const hierarchyParents = useMemo(
    () =>
      (relationTypes ?? []).filter(
        (r) =>
          urnShortName(r.source_object_type_urn) === type && hasTypeClass(r.type_classes, "hierarchy", "parent"),
      ),
    [relationTypes, type],
  );

  const fkFieldTargets = useMemo(() => {
    const map = new Map<string, string>();
    (relationTypes ?? [])
      .filter((r) => urnShortName(r.source_object_type_urn) === type)
      .forEach((r) => map.set(camelToSnake(r.source_property), urnShortName(r.target_object_type_urn)));
    return map;
  }, [relationTypes, type]);

  const relatedLinks = useMemo(
    () => buildRelatedLinksForObjectType(type, relationTypes ?? []),
    [relationTypes, type],
  );

  const mediaItems = useMemo(
    () =>
      object
        ? collectObjectMediaItems(object as Record<string, unknown>, objectType, sharedPropertyTypes)
        : [],
    [object, objectType, sharedPropertyTypes],
  );

  const inlineEditableBySourceKey = useMemo(
    () => computeInlineEditableActions(type, objectType?.implements ?? [], actions ?? []),
    [type, objectType, actions],
  );

  const inlineBaseTypeBySourceKey = useMemo(() => {
    const map = new Map<string, string | undefined>();
    for (const [key, action] of inlineEditableBySourceKey) {
      const vtName = action.parameters?.[0]?.value_type;
      map.set(key, valueTypes.find((vt) => vt.name === vtName)?.base_type);
    }
    return map;
  }, [inlineEditableBySourceKey, valueTypes]);

  const formatsBySourceKey = useMemo(() => {
    return buildFormatsBySourceKey(
      objectType?.property_formats,
      objectType?.property_types,
      objectType?.property_mapping,
      sharedPropertyTypes,
    );
  }, [objectType, sharedPropertyTypes]);

  const conditionalFormatsBySourceKey = useMemo(() => {
    const map = new Map<string, ConditionalFormatRule[]>();
    Object.entries(objectType?.conditional_formats ?? {}).forEach(([property, rules]) => map.set(camelToSnake(property), rules));
    return map;
  }, [objectType]);

  const principalsByUrn = useMemo(() => {
    const map = new Map<string, string>();
    (principals ?? []).forEach((p) => map.set(p.urn, p.display_name));
    return map;
  }, [principals]);

  const nextRevertibleId = useMemo(() => {
    const latestEditBearing = (timeline ?? []).find((e) => e.kind === "invoked" && e.has_edits && !e.reverted);
    return latestEditBearing?.revertible ? latestEditBearing.id : null;
  }, [timeline]);

  function selectAction(actionName: string) {
    const action = relevantActions.find((a) => a.name === actionName);
    setActiveActionName(actionName);
    setActionParameters(
      prefillActionParameters(action?.parameters, {
        principalUrn,
        currentObjectId: String(id),
        currentObject: object as Record<string, unknown> | undefined,
      }),
    );
  }

  function navigateView(next: { tab?: ObjectViewTab; view?: ObjectViewMode }) {
    const tab = next.tab ?? selectedTab;
    const view = next.view ?? viewMode;
    const search: { tab?: string; view?: ObjectViewMode } = {};
    if (view === "configured") {
      search.view = "configured";
      if (tab && tab !== configuredDefinition?.tabs[0]?.id) search.tab = tab;
    } else {
      if (configuredDefinition) search.view = "standard";
      if (tab && tab !== "overview") search.tab = tab;
    }
    void navigate({
      to: "/objects/$type/$id",
      params: { type, id },
      search,
    });
  }

  function setTab(tab: ObjectViewTab) {
    navigateView({ tab });
  }

  function setViewMode(mode: ObjectViewMode) {
    setPreferredMode(type, mode);
    const tab =
      mode === "configured"
        ? configuredDefinition?.tabs[0]?.id ?? ensureDefault(type).tabs[0]?.id
        : "overview";
    navigateView({ view: mode, tab });
  }

  function createConfiguredView() {
    const created = ensureDefault(type);
    setPreferredMode(type, "configured");
    setEditorOpen(true);
    navigateView({ view: "configured", tab: created.tabs[0]?.id });
  }

  async function revertInvocationById(invocationId: number) {
    try {
      await revertInvocation.mutateAsync(invocationId);
      setResult({ ok: true, message: "Reverted." });
    } catch (err) {
      setResult({ ok: false, message: getErrorMessage(err) });
    }
  }

  async function submitAction() {
    if (!activeActionName) return;
    try {
      const action = relevantActions.find((a) => a.name === activeActionName);
      const actionName = action?.parameters !== undefined ? activeActionName : activeActionName.split(".")[1];
      const response = await invokeAction.mutateAsync({ id, actionName, reason, parameters: actionParameters });
      const status = (response as { status?: string }).status;
      setResult({
        ok: true,
        message:
          status === "pending_approval"
            ? "Submitted for approval (high-risk Action)."
            : "Applied immediately.",
      });
    } catch (err) {
      setResult({ ok: false, message: getErrorMessage(err) });
    } finally {
      setActiveActionName(null);
      setReason("");
      setActionParameters({});
    }
  }

  async function submitInlineEdit(action: ActionDefinition, value: unknown) {
    const parameterName = action.parameters?.[0]?.name;
    if (!parameterName) return;
    try {
      const actionName = action.parameters !== undefined ? action.name : action.name.split(".")[1];
      await invokeAction.mutateAsync({
        id,
        actionName,
        reason: "Inline edit",
        parameters: { [parameterName]: value },
      });
      setResult({ ok: true, message: "Applied immediately." });
    } catch (err) {
      setResult({ ok: false, message: getErrorMessage(err) });
    }
  }

  if (!object) return null;

  const hostCtx: ObjectViewHostContext = {
    objectTypeName: type,
    objectId: String(id),
    object: object as Record<string, unknown>,
    objectType,
    maskedFields,
    fkFieldTargets,
    formatsBySourceKey,
    conditionalFormatsBySourceKey,
    principalsByUrn,
    sharedPropertyTypes,
    relatedLinks,
    mediaItems,
    timeline,
    nextRevertibleId,
    reverting: revertInvocation.isPending,
    onRevert: (invocationId) => void revertInvocationById(invocationId),
    inlineEditableBySourceKey,
    inlineBaseTypeBySourceKey,
    onInlineEdit: (action, value) => void submitInlineEdit(action, value),
  };

  const configuredTabs = configuredDefinition?.tabs ?? [];
  const showingConfigured = viewMode === "configured" && configuredDefinition != null;

  return (
    <DetailPage
      breadcrumbs={[
        { label: "Objects", to: "/objects" },
        { label: type, to: "/objects/$type", params: { type } },
        { label: displayTitle || String(id) },
      ]}
      title={
        <>
          {iconUrl ? <img src={iconUrl} alt="" className="hl-object-detail-icon" /> : null}
          {displayTitle || `${type} / ${id}`}
        </>
      }
      description={
        <span className="hl-tag-row">
          <Tag minimal intent={showingConfigured ? "success" : "primary"}>
            {showingConfigured ? "Configured Object View" : "Standard Object View"}
          </Tag>
          <Tag minimal>{type}</Tag>
        </span>
      }
      actions={
        <div className="hl-flex-row hl-items-center hl-gap-sm" style={{ flexWrap: "wrap" }}>
          <ObjectActionsBar actions={relevantActions} onSelect={selectAction} variant="header" />
          <ButtonGroup>
            {configuredDefinition ? (
              <>
                <Button
                  active={!showingConfigured}
                  onClick={() => setViewMode("standard")}
                >
                  Standard
                </Button>
                <Button
                  active={showingConfigured}
                  onClick={() => setViewMode("configured")}
                >
                  Configured
                </Button>
              </>
            ) : (
              <Button icon="cog" onClick={createConfiguredView}>
                Create configured view
              </Button>
            )}
            {configuredDefinition && (
              <Button icon="edit" onClick={() => setEditorOpen(true)}>
                Edit view
              </Button>
            )}
            <Button
              icon="diagram-tree"
              onClick={() =>
                void navigate({
                  to: "/lineage/$urn",
                  params: { urn: `hl:${TENANT_ID}:${WORKSPACE_ID}:object-type:${type}` },
                })
              }
            >
              Lineage
            </Button>
            <Button
              icon="graph"
              onClick={() =>
                void navigate({ to: "/objects/$type/$id/graph", params: { type, id } })
              }
            >
              Graph
            </Button>
          </ButtonGroup>
        </div>
      }
    >
      {hierarchyParents.length > 0 && (
        <div className="hl-tag-row hl-mt-sm hl-mb-sm">
          <span className="hl-text-muted-sm">Hierarchy:</span>
          {hierarchyParents.map((r) => (
            <HierarchyParentCrumb key={r.name} type={type} id={id} relation={r} />
          ))}
          <span className="hl-text-muted-sm">→ {displayTitle || id}</span>
        </div>
      )}
      {result && (
        <Callout intent={result.ok ? "success" : "danger"} className="hl-mt-md hl-mb-md">
          {result.message}
        </Callout>
      )}

      {showingConfigured ? (
        <Tabs
          id="object-view-configured-tabs"
          selectedTabId={
            configuredTabs.some((t) => t.id === selectedTab)
              ? selectedTab
              : configuredTabs[0]?.id
          }
          onChange={(tabId) => setTab(String(tabId))}
          renderActiveTabPanelOnly
          className="hl-oe-ov-tabs"
        >
          {configuredTabs.map((tab) => (
            <Tab
              key={tab.id}
              id={tab.id}
              title={tab.title}
              panel={
                <div className="hl-oe-ov-tab-panel">
                  <ObjectViewWidgetHost tab={tab} ctx={hostCtx} />
                </div>
              }
            />
          ))}
        </Tabs>
      ) : (
      <Tabs
        id="object-view-tabs"
        selectedTabId={selectedTab}
        onChange={(id) => setTab(String(id) as ObjectViewTab)}
        renderActiveTabPanelOnly
        className="hl-oe-ov-tabs"
      >
        <Tab
          id="overview"
          title="Overview"
          panel={
            <div className="hl-oe-ov-tab-panel">
              <ObjectViewOverview
                object={object}
                objectType={objectType}
                objectTypeName={type}
                maskedFields={maskedFields}
                fkFieldTargets={fkFieldTargets}
                formatsBySourceKey={formatsBySourceKey}
                conditionalFormatsBySourceKey={conditionalFormatsBySourceKey}
                principalsByUrn={principalsByUrn}
                sharedPropertyTypes={sharedPropertyTypes}
              />
              <ObjectActionsBar actions={relevantActions} onSelect={selectAction} title="Actions" />
              {relatedLinks.length > 0 && (
                <div className="hl-section">
                  <div className="hl-flex-between hl-items-center">
                    <h4 className="hl-section-title" style={{ margin: 0 }}>
                      Links
                    </h4>
                    <Button minimal small onClick={() => setTab("links")}>
                      View all ({relatedLinks.length})
                    </Button>
                  </div>
                  <div className="hl-tag-row hl-mt-sm">
                    {relatedLinks.slice(0, 8).map((link, i) => (
                      <Tag
                        key={`${link.linkName}-${i}`}
                        minimal
                        intent={link.visibility === "prominent" ? "primary" : "none"}
                        icon="link"
                      >
                        {link.pluralLabel || link.label}
                      </Tag>
                    ))}
                  </div>
                </div>
              )}
            </div>
          }
        />
        <Tab
          id="properties"
          title="Properties"
          panel={
            <div className="hl-oe-ov-tab-panel">
              <ObjectPropertiesTable
                object={object}
                objectType={objectType}
                maskedFields={maskedFields}
                fkFieldTargets={fkFieldTargets}
                formatsBySourceKey={formatsBySourceKey}
                conditionalFormatsBySourceKey={conditionalFormatsBySourceKey}
                principalsByUrn={principalsByUrn}
                sharedPropertyTypes={sharedPropertyTypes}
                inlineEditableBySourceKey={inlineEditableBySourceKey}
                inlineBaseTypeBySourceKey={inlineBaseTypeBySourceKey}
                onInlineEdit={(action, value) => void submitInlineEdit(action, value)}
              />
            </div>
          }
        />
        <Tab
          id="links"
          title={`Links${relatedLinks.length ? ` (${relatedLinks.length})` : ""}`}
          panel={
            <div className="hl-oe-ov-tab-panel">
              {relatedLinks.length === 0 ? (
                <p className="hl-text-muted">No RelationTypes linked to this ObjectType.</p>
              ) : (
                relatedLinks.map((link, i) => (
                  <RelatedLinkPanel
                    key={`${link.linkName}-${i}`}
                    type={type}
                    id={String(id)}
                    link={link}
                    defaultExpanded={link.visibility === "prominent"}
                    writable
                  />
                ))
              )}
            </div>
          }
        />
        <Tab
          id="media"
          title={`Media${mediaItems.length ? ` (${mediaItems.length})` : ""}`}
          panel={
            <div className="hl-oe-ov-tab-panel">
              <ObjectMediaGallery items={mediaItems} />
            </div>
          }
        />
        <Tab
          id="timeline"
          title="Timeline"
          panel={
            <div className="hl-oe-ov-tab-panel">
              {timeline ? (
                <ObjectTimelinePanel
                  timeline={timeline}
                  principalsByUrn={principalsByUrn}
                  nextRevertibleId={nextRevertibleId}
                  reverting={revertInvocation.isPending}
                  onRevert={(invocationId) => void revertInvocationById(invocationId)}
                />
              ) : (
                <p className="hl-text-muted">No timeline events.</p>
              )}
            </div>
          }
        />
        <Tab
          id="graph"
          title="Graph"
          panel={
            <div className="hl-oe-ov-tab-panel">
              <Callout icon="graph" className="hl-mb-md">
                Explore related instances in the neighborhood graph (2–3 hops).
              </Callout>
              <Button
                intent="primary"
                icon="graph"
                onClick={() =>
                  void navigate({ to: "/objects/$type/$id/graph", params: { type, id } })
                }
              >
                Open related instances graph
              </Button>
            </div>
          }
        />
      </Tabs>
      )}

      <ActionInvokeDialog
        action={activeAction}
        reason={reason}
        parameters={actionParameters}
        loading={invokeAction.isPending}
        currentObjectId={String(id)}
        currentObject={object as Record<string, unknown> | undefined}
        onReasonChange={setReason}
        onParametersChange={setActionParameters}
        onClose={() => setActiveActionName(null)}
        onSubmit={() => void submitAction()}
      />

      <ObjectViewEditorDialog
        isOpen={editorOpen}
        objectType={type}
        objectKeys={Object.keys(object as Record<string, unknown>)}
        initial={configuredDefinition}
        onClose={() => setEditorOpen(false)}
        onSave={(definition) => {
          upsertDefinition(definition);
          setPreferredMode(type, "configured");
          navigateView({ view: "configured", tab: definition.tabs[0]?.id });
        }}
        onDelete={() => {
          deleteDefinition(type);
          setEditorOpen(false);
          navigateView({ view: "standard", tab: "overview" });
        }}
      />
    </DetailPage>
  );
}
