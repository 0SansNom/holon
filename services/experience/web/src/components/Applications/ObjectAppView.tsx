import { Suspense, useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Button, Callout, Dialog, DialogBody, DialogFooter, InputGroup, Tag } from "@blueprintjs/core";
import {
  useObjectAppData,
  useObjectAppDetail,
  useInvokeObjectAppAction,
  useObjectType,
  useActions,
  useRelationTypes,
  useSharedPropertyTypes,
  usePrincipals,
} from "../../api/hooks";
import { EmptyState } from "../common/ListPrimitives";
import { ActionParameterFields } from "../common/ActionParameterFields";
import { SkeletonBlock } from "../common/Skeleton";
import type { Application } from "../../api/experience";
import type { ActionDefinition, PropertyFormatRule, ConditionalFormatRule, SharedPropertyType } from "../../api/knowledge";
import { objectSetBrowsePath, titleOf, urnShortName, type RelatedLink, OBJECT_METADATA_KEYS } from "../ObjectExplorer/objectExplorerUtils";
import { RelatedLinkPanel } from "../ObjectExplorer/RelatedLinkPanel";
import { ObjectPropertiesTable } from "../ObjectExplorer/ObjectPropertiesTable";
import {
  buildFormatsBySourceKey,
  resolveDisplayTypeRule,
  resolvePropertyTypeRule,
} from "../Ontology/propertyEditorUtils";
import { camelToSnake } from "../common/propertyFormatUtils";
import { FormattedValue } from "../common/PropertyFormat";
import { useAuthStore } from "../../store/auth";
import { prefillActionParameters } from "../ObjectExplorer/actionParameterPrefill";
import { hasTypeClass } from "../Ontology/typeClassUtils";
import { declaredRelatedLinks, EMPTY_OBJECT_APP_LINKS, resolveObjectAppSurface } from "./objectAppSurface";

const METADATA_KEYS = OBJECT_METADATA_KEYS;

export function ObjectAppView({ application }: { application: Application }) {
  const resolvedSurface = useMemo(() => resolveObjectAppSurface(application), [application]);
  const surfaceObjectType = resolvedSurface?.objectType ?? null;
  const surfaceObjectSet = resolvedSurface?.objectSet;
  const surfaceLinks = useMemo(() => resolvedSurface?.links ?? EMPTY_OBJECT_APP_LINKS, [resolvedSurface]);
  const [selectedId, setSelectedId] = useState<string | number | undefined>(undefined);
  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [actionParameters, setActionParameters] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const principalUrn = useAuthStore((s) => s.session?.principal.urn);

  const { data: objectType } = useObjectType(surfaceObjectType ?? "");
  const { data: allActions = [] } = useActions();
  const { data: sharedPropertyTypes = [] } = useSharedPropertyTypes();
  const { data: relationTypes = [] } = useRelationTypes();
  const { data: principals } = usePrincipals();

  const principalsByUrn = useMemo(() => {
    const map = new Map<string, string>();
    (principals ?? []).forEach((p) => map.set(p.urn, p.display_name));
    return map;
  }, [principals]);

  const fkFieldTargets = useMemo(() => {
    if (!surfaceObjectType) return new Map<string, string>();
    const map = new Map<string, string>();
    relationTypes
      .filter((r) => urnShortName(r.source_object_type_urn) === surfaceObjectType)
      .forEach((r) => map.set(camelToSnake(r.source_property), urnShortName(r.target_object_type_urn)));
    return map;
  }, [relationTypes, surfaceObjectType]);

  const surface = useMemo<{ objectType: string; actions: string[]; links: RelatedLink[] } | null>(() => {
    if (!surfaceObjectType) return null;
    const declaredFullNames = new Set(application.definition.actionRefs.map((a) => a.action));
    const implementedInterfaces = objectType?.implements ?? [];
    const actions = (allActions as ActionDefinition[])
      .filter(
        (a) =>
          declaredFullNames.has(a.name) &&
          (a.target_object_type === surfaceObjectType ||
            (a.target_interface && implementedInterfaces.includes(a.target_interface))) &&
          !hasTypeClass(a.type_classes, "hubble-oe", "hide-action"),
      )
      .map((a) => a.name.split(".").slice(1).join("."));
    return {
      objectType: surfaceObjectType,
      actions,
      links: declaredRelatedLinks(surfaceObjectType, surfaceLinks, relationTypes),
    };
  }, [surfaceObjectType, application.definition.actionRefs, allActions, objectType, surfaceLinks, relationTypes]);
  const { data: rows } = useObjectAppData(surface ? application.name : undefined);
  const invokeAction = useInvokeObjectAppAction(application.name);
  const browseSetPath =
    surfaceObjectType && surfaceObjectSet
      ? objectSetBrowsePath(surfaceObjectType, surfaceObjectSet)
      : null;

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

  async function submitAction() {
    if (!activeAction || selectedId === undefined) return;
    try {
      const response = await invokeAction.mutateAsync({
        id: selectedId,
        actionName: activeAction,
        reason,
        parameters: actionParameters,
      });
      const status = (response as { status?: string }).status;
      setResult({
        ok: true,
        message:
          status === "pending_approval"
            ? "Submitted for approval (high-risk Action)."
            : "Applied.",
      });
    } catch (err) {
      setResult({ ok: false, message: err instanceof Error ? err.message : "Action failed" });
    } finally {
      setActiveAction(null);
      setReason("");
      setActionParameters({});
    }
  }

  if (application.status !== "promoted") {
    return <EmptyState>Promote the application to use it.</EmptyState>;
  }
  if (!surface) {
    return <EmptyState>No objectApp surface configured — enable one on the Builder tab.</EmptyState>;
  }

  const columns = rows && rows.length > 0 ? Object.keys(rows[0]).filter((k) => !METADATA_KEYS.has(k)) : [];

  return (
    <div className="hl-object-app-layout">
      <div className="hl-panel hl-table-scroll hl-object-app-table">
        {surfaceObjectSet && browseSetPath && (
          <div className="hl-flex-row hl-items-center hl-gap-sm hl-mb-sm">
            <Tag minimal intent="primary" icon="filter">
              Object Set
            </Tag>
            <Link to={browseSetPath.to} params={browseSetPath.params} search={browseSetPath.search}>
              {surfaceObjectSet}
            </Link>
            <span className="hl-text-muted-sm">List is PDP-gated via evaluate.</span>
          </div>
        )}
        <table className="hl-data-table hl-data-table-compact">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(rows ?? []).map((row) => {
              const id = row.id as string | number;
              return (
                <tr
                  key={String(id)}
                  className="hl-data-table-row"
                  data-selected={selectedId === id}
                  onClick={() => setSelectedId(id)}
                >
                  {columns.map((col) => (
                    <td key={col}>
                      <FormattedValue
                        rule={formatsBySourceKey.get(col)}
                        value={row[col]}
                        typeRule={resolveDisplayTypeRule(
                          resolvePropertyTypeRule(col, objectType?.property_types, objectType?.property_mapping),
                          sharedPropertyTypes,
                        )}
                        compact
                      />
                    </td>
                  ))}
                </tr>
              );
            })}
            {rows?.length === 0 && (
              <tr>
                <td colSpan={Math.max(columns.length, 1)} className="hl-data-table-empty">
                  No instances.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="hl-panel hl-object-app-detail">
        {selectedId === undefined && <p className="hl-text-muted">Select a row to see its detail.</p>}
        {selectedId !== undefined && (
          <Suspense
            fallback={
              <div aria-busy aria-label="Loading detail">
                {Array.from({ length: 5 }, (_, i) => (
                  <div key={i} className="hl-flex-between hl-mb-xs">
                    <SkeletonBlock width={80} height={14} />
                    <SkeletonBlock width="55%" height={14} />
                  </div>
                ))}
              </div>
            }
          >
            <ObjectAppDetailPanel
              application={application}
              surface={surface}
              selectedId={selectedId}
              formatsBySourceKey={formatsBySourceKey}
              conditionalFormatsBySourceKey={conditionalFormatsBySourceKey}
              sharedPropertyTypes={sharedPropertyTypes}
              objectType={objectType}
              principalsByUrn={principalsByUrn}
              fkFieldTargets={fkFieldTargets}
              result={result}
              onStartAction={(localName) => {
                const action =
                  allActions.find((a) => a.name === `${surface.objectType}.${localName}`) ??
                  allActions.find((a) => a.name.endsWith(`.${localName}`));
                const currentObject =
                  (rows ?? []).find((r) => String(r.id) === String(selectedId)) ?? null;
                setActiveAction(localName);
                setActionParameters(
                  prefillActionParameters(action?.parameters, {
                    principalUrn,
                    currentObjectId: selectedId !== undefined ? String(selectedId) : undefined,
                    currentObject: currentObject as Record<string, unknown> | null,
                  }),
                );
              }}
            />
          </Suspense>
        )}
      </div>

      <Dialog isOpen={activeAction !== null} onClose={() => setActiveAction(null)} title={activeAction ?? ""}>
        <DialogBody>
          <ActionParameterFields
            parameters={allActions.find((a) => a.name === `${surface?.objectType}.${activeAction}`)?.parameters ?? []}
            values={actionParameters}
            onChange={setActionParameters}
            sections={allActions.find((a) => a.name === `${surface?.objectType}.${activeAction}`)?.sections}
            currentObjectId={selectedId !== undefined ? String(selectedId) : undefined}
            currentObject={
              ((rows ?? []).find((r) => String(r.id) === String(selectedId)) as
                | Record<string, unknown>
                | undefined) ?? null
            }
          />
          <InputGroup placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
        </DialogBody>
        <DialogFooter
          actions={
            <Button intent="primary" loading={invokeAction.isPending} onClick={() => void submitAction()}>
              Submit
            </Button>
          }
        />
      </Dialog>
    </div>
  );
}

function ObjectAppDetailPanel({
  application,
  surface,
  selectedId,
  formatsBySourceKey,
  conditionalFormatsBySourceKey,
  sharedPropertyTypes,
  objectType,
  principalsByUrn,
  fkFieldTargets,
  result,
  onStartAction,
}: {
  application: Application;
  surface: { objectType: string; actions: string[]; links: RelatedLink[] };
  selectedId: string | number;
  formatsBySourceKey: Map<string, PropertyFormatRule>;
  conditionalFormatsBySourceKey: Map<string, ConditionalFormatRule[]>;
  sharedPropertyTypes: SharedPropertyType[];
  objectType: ReturnType<typeof useObjectType>["data"];
  principalsByUrn: Map<string, string>;
  fkFieldTargets: Map<string, string>;
  result: { ok: boolean; message: string } | null;
  onStartAction: (localName: string) => void;
}) {
  const { data: detail } = useObjectAppDetail(application.name, selectedId);
  const maskedFields = (detail?._maskedFields as string[] | undefined) ?? [];
  const displayTitle = titleOf(detail as Record<string, unknown> | undefined, objectType);

  return (
    <div>
      <div className="hl-flex-between hl-items-start hl-mb-sm">
        <div>
          <div className="hl-body-text" style={{ fontWeight: 600 }}>
            {displayTitle || String(selectedId)}
          </div>
          <div className="hl-mono hl-text-muted-sm">
            {surface.objectType}/{String(selectedId)}
          </div>
        </div>
        <Link
          to="/objects/$type/$id"
          params={{ type: surface.objectType, id: String(selectedId) }}
          className="hl-link-accent"
        >
          Open Object View
        </Link>
      </div>

      {result && (
        <Callout intent={result.ok ? "success" : "danger"} className="hl-mb-md">
          {result.message}
        </Callout>
      )}

      <ObjectPropertiesTable
        object={detail as Record<string, unknown>}
        objectType={objectType}
        maskedFields={maskedFields}
        fkFieldTargets={fkFieldTargets}
        formatsBySourceKey={formatsBySourceKey}
        conditionalFormatsBySourceKey={conditionalFormatsBySourceKey}
        principalsByUrn={principalsByUrn}
        sharedPropertyTypes={sharedPropertyTypes}
      />

      {surface.links.length > 0 && (
        <div className="hl-mt-md">
          <div className="hl-section-title hl-mb-sm">Related</div>
          {surface.links.map((link) => (
            <RelatedLinkPanel key={link.linkName} type={surface.objectType} id={String(selectedId)} link={link} />
          ))}
        </div>
      )}
      {surface.actions.length > 0 && (
        <div className="hl-action-bar">
          {surface.actions.map((localName) => (
            <Button key={localName} small intent="primary" onClick={() => onStartAction(localName)}>
              {localName}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
