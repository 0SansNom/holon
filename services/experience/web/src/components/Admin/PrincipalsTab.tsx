import { useMemo, useState } from "react";
import { Button, Callout, Card, Dialog, DialogBody, DialogFooter, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import { usePrincipals, useGrantWorkspaceAccess, useRevokeWorkspaceAccess } from "../../api/hooks";
import type { IdentityPrincipal, AccessRelation } from "../../api/identity";
import { ApiError } from "../../api/client";
import { CardGrid, EmptyState, ErrorCallout } from "../common/ListPrimitives";
import { OntologyTabHeader } from "../Ontology/OntologyTabLayout";

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
        <p className="hl-mono hl-text-muted">{principal.urn}</p>
        <p className="hl-text-muted">
          Grants/revokes are workspace-tier governance — only a principal already holding workspace{" "}
          <code>approve</code> (admin) can do this; everyone else gets a 403 from Identity.
        </p>
        <HTMLSelect
          fill
          value={relation}
          onChange={(e) => setRelation(e.target.value as AccessRelation)}
          options={RELATIONS}
        />
        {error && <ErrorCallout>{error}</ErrorCallout>}
        {ok && (
          <Callout intent="success" className="hl-mt-sm">
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
  const { data } = usePrincipals();
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

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            Every seeded principal in this tenant — agents and service accounts included, not just human users.
            Workspace-level access (the base ReBAC grant every ObjectType's <code>read</code>/<code>write</code>/
            <code>approve</code> permission cascades from) is managed here.
          </>
        }
      />

      <div className="hl-flex-row hl-gap-sm hl-mb-sm">
        <InputGroup
          leftIcon="filter"
          placeholder="Filter by name or URN..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="hl-filter-input"
        />
        <HTMLSelect value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">All types</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </HTMLSelect>
        <Tag minimal className="hl-self-center">
          {filtered.length} of {data?.length ?? 0}
        </Tag>
      </div>

      <CardGrid minWidth={280}>
        {filtered.map((principal) => (
          <Card key={principal.urn}>
            <div className="hl-registry-card-header">
              <div className="hl-min-w-0">
                <strong className="hl-registry-card-title">{principal.display_name}</strong>
                <div className="hl-mono hl-text-muted-sm hl-mt-xs">{principal.urn}</div>
              </div>
              <Tag minimal>{principal.type}</Tag>
            </div>
            <div className="hl-card-footer">
              <span className="hl-text-muted-sm">{principal.country ?? "—"}</span>
              <Button small minimal icon="key" onClick={() => setManaging(principal)}>
                Manage access
              </Button>
            </div>
          </Card>
        ))}
        {filtered.length === 0 && <EmptyState>No principals match this filter.</EmptyState>}
      </CardGrid>
      {managing && <ManageAccessDialog principal={managing} onClose={() => setManaging(null)} />}
    </div>
  );
}
