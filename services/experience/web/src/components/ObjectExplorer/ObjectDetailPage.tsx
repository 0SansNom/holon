import { useMemo, useState } from "react";
import { useParams, useNavigate } from "@tanstack/react-router";
import { Button, ButtonGroup, Callout } from "@blueprintjs/core";
import {
  useActions,
  useObject,
  useObjectType,
  useInvokeAction,
  useRelationTypes,
  usePrincipals,
  useObjectTimeline,
  useRevertActionInvocation,
} from "../../api/hooks";
import { getErrorMessage } from "../../api/client";
import { camelToSnake } from "../common/propertyFormatUtils";
import { DetailPage } from "../common/PageLayout";
import type { PropertyFormatRule, ConditionalFormatRule } from "../../api/knowledge";
import { TENANT_ID, WORKSPACE_ID } from "../../api/config";
import { titleOf, urnShortName, type RelatedLink } from "./objectExplorerUtils";
import { RelatedLinkPanel } from "./RelatedLinkPanel";
import { ObjectPropertiesTable } from "./ObjectPropertiesTable";
import { ObjectActionsBar } from "./ObjectActionsBar";
import { ObjectTimelinePanel } from "./ObjectTimelinePanel";
import { ActionInvokeDialog } from "./ActionInvokeDialog";

export function ObjectDetailPage() {
  const { type, id } = useParams({ from: "/shell/objects/$type/$id" });
  const navigate = useNavigate();
  const { data: object } = useObject(type, id);
  const { data: objectType } = useObjectType(type);
  const displayTitle = titleOf(object as Record<string, unknown> | undefined, objectType);
  const { data: actions } = useActions();
  const { data: relationTypes } = useRelationTypes();
  const { data: principals } = usePrincipals();
  const { data: timeline } = useObjectTimeline(type, id);
  const invokeAction = useInvokeAction(type);
  const revertInvocation = useRevertActionInvocation(type, id);

  const [activeActionName, setActiveActionName] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [actionParameters, setActionParameters] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const relevantActions = (actions ?? []).filter(
    (a) => a.target_object_type === type || (a.target_interface && (objectType?.implements ?? []).includes(a.target_interface)),
  );
  const activeAction = relevantActions.find((a) => a.name === activeActionName);
  const maskedFields = (object?._maskedFields as string[] | undefined) ?? [];

  const fkFieldTargets = useMemo(() => {
    const map = new Map<string, string>();
    (relationTypes ?? [])
      .filter((r) => urnShortName(r.source_object_type_urn) === type)
      .forEach((r) => map.set(camelToSnake(r.source_property), urnShortName(r.target_object_type_urn)));
    return map;
  }, [relationTypes, type]);

  const relatedLinks = useMemo<RelatedLink[]>(() => {
    const links: RelatedLink[] = [];
    (relationTypes ?? []).forEach((r) => {
      const sourceType = urnShortName(r.source_object_type_urn);
      const targetType = urnShortName(r.target_object_type_urn);
      if (sourceType === type) {
        const localName = r.name.includes(".") ? r.name.split(".").slice(1).join(".") : r.name;
        links.push({ linkName: localName, label: `${localName} → ${targetType}`, relatedType: targetType });
      }
      if (targetType === type && r.target_property) {
        links.push({ linkName: r.target_property, label: `${r.target_property} ← ${sourceType}`, relatedType: sourceType });
      }
    });
    return links;
  }, [relationTypes, type]);

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

  const principalsByUrn = useMemo(() => {
    const map = new Map<string, string>();
    (principals ?? []).forEach((p) => map.set(p.urn, p.display_name));
    return map;
  }, [principals]);

  const nextRevertibleId = useMemo(() => {
    const latestEditBearing = (timeline ?? []).find((e) => e.kind === "invoked" && e.has_edits && !e.reverted);
    return latestEditBearing?.revertible ? latestEditBearing.id : null;
  }, [timeline]);

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
        message: status === "pending" ? "Submitted for approval (high-risk Action)." : "Applied immediately.",
      });
    } catch (err) {
      setResult({ ok: false, message: getErrorMessage(err) });
    } finally {
      setActiveActionName(null);
      setReason("");
      setActionParameters({});
    }
  }

  if (!object) return null;

  return (
    <DetailPage
      breadcrumbs={[
        { label: "Objects", to: "/objects" },
        { label: type, to: "/objects/$type", params: { type } },
        { label: displayTitle || String(id) },
      ]}
      title={displayTitle || `${type} / ${id}`}
      actions={
        <ButtonGroup>
          <Button
            icon="diagram-tree"
            onClick={() => void navigate({ to: "/lineage/$urn", params: { urn: `hl:${TENANT_ID}:${WORKSPACE_ID}:object-type:${type}` } })}
          >
            Lineage
          </Button>
          <Button icon="graph" onClick={() => void navigate({ to: "/objects/$type/$id/graph", params: { type, id } })}>
            Related instances
          </Button>
        </ButtonGroup>
      }
    >
      {result && (
        <Callout intent={result.ok ? "success" : "danger"} className="hl-mt-md">
          {result.message}
        </Callout>
      )}

      <ObjectPropertiesTable
        object={object}
        objectType={objectType}
        maskedFields={maskedFields}
        fkFieldTargets={fkFieldTargets}
        formatsBySourceKey={formatsBySourceKey}
        conditionalFormatsBySourceKey={conditionalFormatsBySourceKey}
        principalsByUrn={principalsByUrn}
      />

      <ObjectActionsBar
        actions={relevantActions}
        onSelect={(actionName) => {
          setActiveActionName(actionName);
          setActionParameters({});
        }}
      />

      {relatedLinks.length > 0 && (
        <div className="hl-section">
          <h4 className="hl-section-title">Related objects</h4>
          {relatedLinks.map((link, i) => (
            <RelatedLinkPanel key={`${link.linkName}-${i}`} type={type} id={String(id)} link={link} />
          ))}
        </div>
      )}

      {timeline && (
        <ObjectTimelinePanel
          timeline={timeline}
          principalsByUrn={principalsByUrn}
          nextRevertibleId={nextRevertibleId}
          reverting={revertInvocation.isPending}
          onRevert={(invocationId) => void revertInvocationById(invocationId)}
        />
      )}

      <ActionInvokeDialog
        action={activeAction}
        reason={reason}
        parameters={actionParameters}
        loading={invokeAction.isPending}
        onReasonChange={setReason}
        onParametersChange={setActionParameters}
        onClose={() => setActiveActionName(null)}
        onSubmit={() => void submitAction()}
      />
    </DetailPage>
  );
}
