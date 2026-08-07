import { useMemo, useState } from "react";
import { useParams, useNavigate, Link } from "@tanstack/react-router";
import { Button, ButtonGroup, Dialog, DialogBody, DialogFooter, H3, InputGroup, Spinner, Tag, Callout } from "@blueprintjs/core";
import { useActions, useObject, useObjectType, useInvokeAction, useRelationTypes } from "../../api/hooks";
import { camelToSnake, FormattedValue } from "../common/PropertyFormat";
import { PageBreadcrumbs } from "../common/PageBreadcrumbs";
import type { PropertyFormatRule } from "../../api/knowledge";

const METADATA_KEYS = new Set(["materializedAt", "sourceLagSeconds", "degraded", "_maskedFields"]);

function urnShortName(urn: string): string {
  const parts = urn.split(":");
  return parts[parts.length - 1] ?? urn;
}

export function ObjectDetailPage() {
  const { type, id } = useParams({ from: "/shell/objects/$type/$id" });
  const navigate = useNavigate();
  const { data: object, isLoading, error } = useObject(type, id);
  const { data: objectType } = useObjectType(type);
  const { data: actions } = useActions();
  const { data: relationTypes } = useRelationTypes();
  const invokeAction = useInvokeAction(type);

  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const relevantActions = (actions ?? []).filter((a) => a.target_object_type === type);
  const maskedFields = (object?._maskedFields as string[] | undefined) ?? [];

  // Foreign-key fields (e.g. an Order's customer_id) are otherwise dead
  // text — the RelationType metadata already knows they point at another
  // ObjectType's instance, so surface that as a real link instead of
  // requiring a manual detour through the Objects list to find it.
  const fkFieldTargets = useMemo(() => {
    const map = new Map<string, string>();
    (relationTypes ?? [])
      .filter((r) => urnShortName(r.source_object_type_urn) === type)
      .forEach((r) => map.set(camelToSnake(r.source_property), urnShortName(r.target_object_type_urn)));
    return map;
  }, [relationTypes, type]);

  const formatsBySourceKey = useMemo(() => {
    const map = new Map<string, PropertyFormatRule>();
    Object.entries(objectType?.property_formats ?? {}).forEach(([property, rule]) => map.set(camelToSnake(property), rule));
    return map;
  }, [objectType]);

  async function submitAction() {
    if (!activeAction) return;
    try {
      const localName = activeAction.split(".")[1];
      const response = await invokeAction.mutateAsync({ id, actionName: localName, reason });
      const status = (response as { status?: string }).status;
      setResult({
        ok: true,
        message: status === "pending" ? "Submitted for approval (high-risk Action)." : "Applied immediately.",
      });
    } catch (err) {
      setResult({ ok: false, message: err instanceof Error ? err.message : "Action failed" });
    } finally {
      setActiveAction(null);
      setReason("");
    }
  }

  if (isLoading) return <Spinner />;
  if (error) return <p style={{ color: "var(--hl-danger)" }}>{(error as Error).message}</p>;
  if (!object) return null;

  return (
    <div>
      <PageBreadcrumbs
        items={[
          { label: "Objects", to: "/objects" },
          { label: type, to: "/objects/$type", params: { type } },
          { label: String(id) },
        ]}
      />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 6 }}>
        <H3 style={{ margin: 0 }}>
          {type} / {id}
        </H3>
        <ButtonGroup>
          <Button
            icon="diagram-tree"
            onClick={() => void navigate({ to: "/lineage/$urn", params: { urn: `hl:acme:demo:object-type:${type}` } })}
          >
            Lineage
          </Button>
          <Button icon="graph" onClick={() => void navigate({ to: "/objects/$type/$id/graph", params: { type, id } })}>
            Related instances
          </Button>
        </ButtonGroup>
      </div>

      {result && (
        <Callout intent={result.ok ? "success" : "danger"} style={{ marginTop: 16 }}>
          {result.message}
        </Callout>
      )}

      <div className="hl-panel" style={{ marginTop: 16 }}>
        <table style={{ width: "100%", fontSize: 13 }}>
          <tbody>
            {Object.entries(object)
              .filter(([key]) => !METADATA_KEYS.has(key))
              .map(([key, value]) => {
                const fkTargetType = fkFieldTargets.get(key);
                return (
                  <tr key={key} style={{ borderBottom: "1px solid var(--hl-border)" }}>
                    <td style={{ padding: "8px 12px", color: "var(--hl-text-muted)", width: 200 }}>{key}</td>
                    <td style={{ padding: "8px 12px" }}>
                      {maskedFields.includes(key) ? (
                        <span className="hl-masked-field">forbidden — masked by permission</span>
                      ) : value !== null && fkTargetType ? (
                        <Link
                          to="/objects/$type/$id"
                          params={{ type: fkTargetType, id: String(value) }}
                          className="hl-mono"
                          style={{ color: "var(--hl-accent)" }}
                        >
                          {String(value)} → {fkTargetType}
                        </Link>
                      ) : (
                        <FormattedValue rule={formatsBySourceKey.get(key)} value={value} />
                      )}
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>

      {relevantActions.length > 0 && (
        <div style={{ marginTop: 20 }}>
          <h4 style={{ fontSize: 13, color: "var(--hl-text-muted)", textTransform: "uppercase", letterSpacing: "0.03em" }}>
            Actions
          </h4>
          <div style={{ display: "flex", gap: 8 }}>
            {relevantActions.map((a) => (
              <Button key={a.name} intent={a.risk_level === "high" ? "danger" : "primary"} onClick={() => setActiveAction(a.name)}>
                {a.name.split(".")[1]}
                <Tag minimal style={{ marginLeft: 8 }}>
                  {a.risk_level}
                </Tag>
              </Button>
            ))}
          </div>
        </div>
      )}

      <Dialog isOpen={activeAction !== null} onClose={() => setActiveAction(null)} title={activeAction ?? ""}>
        <DialogBody>
          <p style={{ fontSize: 13, color: "var(--hl-text-muted)" }}>
            {relevantActions.find((a) => a.name === activeAction)?.risk_level === "high"
              ? "This is a high-risk Action — it will create a pending approval, not apply immediately."
              : "This Action applies immediately."}
          </p>
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
