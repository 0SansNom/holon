import { Suspense, useMemo, useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter, InputGroup } from "@blueprintjs/core";
import { useObjectAppData, useObjectAppDetail, useInvokeObjectAppAction, useObjectType, useActions } from "../../api/hooks";
import { FormattedValue } from "../common/PropertyFormat";
import { applyConditionalStyle, camelToSnake } from "../common/propertyFormatUtils";
import { EmptyState } from "../common/ListPrimitives";
import { ActionParameterFields } from "../common/ActionParameterFields";
import { SkeletonBlock } from "../common/Skeleton";
import type { Application } from "../../api/experience";
import type { ActionDefinition, PropertyFormatRule, ConditionalFormatRule } from "../../api/knowledge";

const METADATA_KEYS = new Set(["materializedAt", "sourceLagSeconds", "degraded", "_maskedFields"]);

function resolveObjectAppObjectType(application: Application): string | null {
  const surface = application.definition.surfaces.find((s) => s.type === "objectApp") as
    | { objectType?: string }
    | undefined;
  return surface?.objectType ?? null;
}

export function ObjectAppView({ application }: { application: Application }) {
  const surfaceObjectType = resolveObjectAppObjectType(application);
  const [selectedId, setSelectedId] = useState<string | number | undefined>(undefined);
  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [actionParameters, setActionParameters] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const { data: objectType } = useObjectType(surfaceObjectType ?? "");
  const { data: allActions = [] } = useActions();

  const surface = useMemo<{ objectType: string; actions: string[] } | null>(() => {
    if (!surfaceObjectType) return null;
    const declaredFullNames = new Set(application.definition.actionRefs.map((a) => a.action));
    const implementedInterfaces = objectType?.implements ?? [];
    const actions = (allActions as ActionDefinition[])
      .filter(
        (a) =>
          declaredFullNames.has(a.name) &&
          (a.target_object_type === surfaceObjectType || (a.target_interface && implementedInterfaces.includes(a.target_interface))),
      )
      .map((a) => a.name.split(".").slice(1).join("."));
    return { objectType: surfaceObjectType, actions };
  }, [surfaceObjectType, application.definition.actionRefs, allActions, objectType]);
  const { data: rows } = useObjectAppData(surface ? application.name : undefined);
  const invokeAction = useInvokeObjectAppAction(application.name);

  const formatsBySourceKey = useMemo(() => {
    const map = new Map<string, PropertyFormatRule>();
    Object.entries(objectType?.property_formats ?? {}).forEach(([property, rule]) => map.set(camelToSnake(property), rule));
    return map;
  }, [objectType]);

  const conditionalFormatsBySourceKey = useMemo(() => {
    const map = new Map<string, ConditionalFormatRule[]>();
    Object.entries(objectType?.conditional_formats ?? {}).forEach(([property, rules]) => map.set(camelToSnake(property), rules));
    return map;
  }, [objectType]);

  async function submitAction() {
    if (!activeAction || selectedId === undefined) return;
    try {
      await invokeAction.mutateAsync({ id: selectedId, actionName: activeAction, reason, parameters: actionParameters });
      setResult({ ok: true, message: "Applied." });
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
                      <FormattedValue rule={formatsBySourceKey.get(col)} value={row[col]} />
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
              result={result}
              setActiveAction={setActiveAction}
              setActionParameters={setActionParameters}
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
  result,
  setActiveAction,
  setActionParameters,
}: {
  application: Application;
  surface: { objectType: string; actions: string[] };
  selectedId: string | number;
  formatsBySourceKey: Map<string, PropertyFormatRule>;
  conditionalFormatsBySourceKey: Map<string, ConditionalFormatRule[]>;
  result: { ok: boolean; message: string } | null;
  setActiveAction: (name: string | null) => void;
  setActionParameters: (params: Record<string, unknown>) => void;
}) {
  const { data: detail } = useObjectAppDetail(application.name, selectedId);

  return (
    <div>
      {result && (
        <Callout intent={result.ok ? "success" : "danger"} className="hl-mb-md">
          {result.message}
        </Callout>
      )}
      <table className="hl-properties-table">
        <tbody>
          {Object.entries(detail)
            .filter(([key]) => !METADATA_KEYS.has(key))
            .map(([key, value]) => (
              <tr key={key} className="hl-properties-row">
                <td className="hl-properties-key">{key}</td>
                <td
                  className="hl-properties-value"
                  style={applyConditionalStyle(conditionalFormatsBySourceKey.get(key), detail, value)}
                >
                  <FormattedValue rule={formatsBySourceKey.get(key)} value={value} />
                </td>
              </tr>
            ))}
        </tbody>
      </table>
      {surface.actions.length > 0 && (
        <div className="hl-action-bar">
          {surface.actions.map((localName) => (
            <Button
              key={localName}
              small
              intent="primary"
              onClick={() => {
                setActiveAction(localName);
                setActionParameters({});
              }}
            >
              {localName}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
