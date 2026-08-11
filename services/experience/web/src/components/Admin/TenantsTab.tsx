import { useMemo, useState } from "react";
import { Button, Card, Dialog, DialogBody, DialogFooter, FormGroup, InputGroup, Tag } from "@blueprintjs/core";
import { useSuspenseQuery } from "@tanstack/react-query";
import {
  useCreateTenant,
  useCreateWorkspace,
  useTenants,
  useWorkspaces,
} from "../../api/hooks";
import { identityApi } from "../../api/identity";
import { ApiError } from "../../api/client";
import { CardGrid, ErrorCallout } from "../common/ListPrimitives";
import { OntologyTabHeader } from "../Ontology/OntologyTabLayout";

export function TenantsTab() {
  const { data } = useTenants();
  const create = useCreateTenant();
  const [open, setOpen] = useState(false);
  const [tenantId, setTenantId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    try {
      await create.mutateAsync({ tenantId, displayName });
      setOpen(false);
      setTenantId("");
      setDisplayName("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    }
  }

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            Tenants are filiales / legal entities on this self-hosted instance (ADR 026). Creating a tenant is
            bootstrap-workspace admin governance — not SaaS multi-customer hosting.
          </>
        }
      />
      <Button intent="primary" icon="plus" className="hl-mb-sm" onClick={() => setOpen(true)}>
        Create tenant
      </Button>
      <CardGrid minWidth={280}>
        {(data ?? []).map((t) => (
          <Card key={t.tenant_id}>
            <div className="hl-registry-card-header">
              <strong>{t.display_name}</strong>
              <Tag minimal>{t.status}</Tag>
            </div>
            <p className="hl-mono hl-text-muted">{t.tenant_id}</p>
          </Card>
        ))}
      </CardGrid>
      <Dialog isOpen={open} title="Create tenant (filiale)" onClose={() => setOpen(false)}>
        <DialogBody>
          <FormGroup label="Tenant id" labelInfo="(slug)">
            <InputGroup value={tenantId} onChange={(e) => setTenantId(e.target.value)} placeholder="filiale-a" />
          </FormGroup>
          <FormGroup label="Display name">
            <InputGroup value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </FormGroup>
          {error && <ErrorCallout>{error}</ErrorCallout>}
        </DialogBody>
        <DialogFooter
          actions={
            <Button intent="primary" loading={create.isPending} onClick={() => void submit()}>
              Create
            </Button>
          }
        />
      </Dialog>
    </div>
  );
}

export function WorkspacesTab() {
  const { data: me } = useSuspenseQuery({ queryKey: ["whoami"], queryFn: identityApi.whoami });
  const { data } = useWorkspaces();
  const create = useCreateWorkspace();
  const [open, setOpen] = useState(false);
  const [tenantId, setTenantId] = useState(me.tenant_id);
  const [workspaceId, setWorkspaceId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [initialAdminUrn, setInitialAdminUrn] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Do not call GET /tenants here — that endpoint is bootstrap-admin only.
  // Options come from workspaces the caller can already see + whoami.
  const tenantOptions = useMemo(() => {
    const ids = new Set((data ?? []).map((w) => w.tenant_id));
    ids.add(me.tenant_id);
    return Array.from(ids).sort();
  }, [data, me.tenant_id]);

  async function submit() {
    setError(null);
    try {
      await create.mutateAsync({
        tenantId,
        workspaceId,
        displayName,
        initialAdminUrn: initialAdminUrn.trim() || undefined,
      });
      setOpen(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    }
  }

  return (
    <div>
      <OntologyTabHeader description={<>Workspaces live inside a tenant (filiale). Teams collaborate here.</>} />
      <Button intent="primary" icon="plus" className="hl-mb-sm" onClick={() => setOpen(true)}>
        Create workspace
      </Button>
      <CardGrid minWidth={280}>
        {(data ?? []).map((w) => (
          <Card key={`${w.tenant_id}:${w.workspace_id}`}>
            <div className="hl-registry-card-header">
              <strong>{w.display_name}</strong>
              <Tag minimal>{w.status}</Tag>
            </div>
            <p className="hl-mono hl-text-muted">
              {w.tenant_id} / {w.workspace_id}
            </p>
          </Card>
        ))}
      </CardGrid>
      <Dialog isOpen={open} title="Create workspace" onClose={() => setOpen(false)}>
        <DialogBody>
          <FormGroup label="Tenant">
            <InputGroup
              value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}
              placeholder={tenantOptions[0] ?? me.tenant_id}
              list="hl-tenant-ids"
            />
            <datalist id="hl-tenant-ids">
              {tenantOptions.map((id) => (
                <option key={id} value={id} />
              ))}
            </datalist>
          </FormGroup>
          <FormGroup label="Workspace id">
            <InputGroup value={workspaceId} onChange={(e) => setWorkspaceId(e.target.value)} placeholder="ops" />
          </FormGroup>
          <FormGroup label="Display name">
            <InputGroup value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </FormGroup>
          <FormGroup
            label="Initial admin URN"
            labelInfo="(required for another tenant)"
            helperText="Filiale principal URN — never grants admin to a cross-tenant instance admin."
          >
            <InputGroup
              value={initialAdminUrn}
              onChange={(e) => setInitialAdminUrn(e.target.value)}
              placeholder="hl:filiale-a:global:user:…"
            />
          </FormGroup>
          {error && <ErrorCallout>{error}</ErrorCallout>}
        </DialogBody>
        <DialogFooter
          actions={
            <Button intent="primary" loading={create.isPending} onClick={() => void submit()}>
              Create
            </Button>
          }
        />
      </Dialog>
    </div>
  );
}
