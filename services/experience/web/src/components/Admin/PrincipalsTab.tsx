import { useMemo, useState } from "react";
import { Button, Callout, Card, Dialog, DialogBody, DialogFooter, HTMLSelect, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import { usePrincipals, useGrantWorkspaceAccess, useRevokeWorkspaceAccess } from "../../api/hooks";
import type { IdentityPrincipal, AccessRelation } from "../../api/identity";
import { ApiError } from "../../api/client";

const RELATIONS: AccessRelation[] = ["viewer", "editor", "admin"];

function ManageAccessDialog({ principal, onClose }: { principal: IdentityPrincipal; onClose: () => void }) {
  const [relation, setRelation] = useState<AccessRelation>("viewer");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const grant = useGrantWorkspaceAccess();
  const revoke = useRevokeWorkspaceAccess();

  async function run(action: "grant" | "revoke") {
    setError(null);
    setOk(null);
    try {
      const mutation = action === "grant" ? grant : revoke;
      await mutation.mutateAsync({ principalUrn: principal.urn, relation });
      setOk(`${action === "grant" ? "Granted" : "Revoked"} workspace ${relation} for ${principal.display_name}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    }
  }

  return (
    <Dialog isOpen title={`Workspace access — ${principal.display_name}`} onClose={onClose}>
      <DialogBody>
        <p className="hl-mono" style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
          {principal.urn}
        </p>
        <p style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
          Grants/revokes are workspace-tier governance — only a principal already holding workspace{" "}
          <code>approve</code> (admin) can do this; everyone else gets a 403 from Identity.
        </p>
        <HTMLSelect
          fill
          value={relation}
          onChange={(e) => setRelation(e.target.value as AccessRelation)}
          options={RELATIONS}
        />
        {error && (
          <Callout intent="danger" style={{ marginTop: 12 }}>
            {error}
          </Callout>
        )}
        {ok && (
          <Callout intent="success" style={{ marginTop: 12 }}>
            {ok}
          </Callout>
        )}
      </DialogBody>
      <DialogFooter
        actions={
          <>
            <Button intent="danger" loading={revoke.isPending} onClick={() => void run("revoke")}>
              Revoke
            </Button>
            <Button intent="primary" loading={grant.isPending} onClick={() => void run("grant")}>
              Grant
            </Button>
          </>
        }
      />
    </Dialog>
  );
}

export function PrincipalsTab() {
  const { data, isLoading } = usePrincipals();
  const [managing, setManaging] = useState<IdentityPrincipal | null>(null);
  const [filter, setFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");

  const types = useMemo(() => Array.from(new Set((data ?? []).map((p) => p.type))).sort(), [data]);

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return (data ?? []).filter((p) => {
      if (typeFilter && p.type !== typeFilter) return false;
      if (!needle) return true;
      return p.display_name.toLowerCase().includes(needle) || p.urn.toLowerCase().includes(needle);
    });
  }, [data, filter, typeFilter]);

  if (isLoading) return <Spinner />;

  return (
    <div>
      <p style={{ fontSize: 12, color: "var(--hl-text-muted)", marginBottom: 12 }}>
        Every seeded principal in this tenant — agents and service accounts included, not just human users.
        Workspace-level access (the base ReBAC grant every ObjectType's <code>read</code>/<code>write</code>/
        <code>approve</code> permission cascades from) is managed here.
      </p>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <InputGroup
          leftIcon="filter"
          placeholder="Filter by name or URN..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ maxWidth: 280 }}
        />
        <HTMLSelect value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">All types</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </HTMLSelect>
        <Tag minimal style={{ alignSelf: "center" }}>
          {filtered.length} of {data?.length ?? 0}
        </Tag>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
        {filtered.map((principal) => (
          <Card key={principal.urn}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start" }}>
              <div>
                <strong>{principal.display_name}</strong>
                <div className="hl-mono" style={{ fontSize: 11, color: "var(--hl-text-muted)", marginTop: 4 }}>
                  {principal.urn}
                </div>
              </div>
              <Tag minimal>{principal.type}</Tag>
            </div>
            <div style={{ marginTop: 10, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 11, color: "var(--hl-text-muted)" }}>{principal.country ?? "—"}</span>
              <Button small minimal icon="key" onClick={() => setManaging(principal)}>
                Manage access
              </Button>
            </div>
          </Card>
        ))}
        {filtered.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No principals match this filter.</p>}
      </div>
      {managing && <ManageAccessDialog principal={managing} onClose={() => setManaging(null)} />}
    </div>
  );
}
