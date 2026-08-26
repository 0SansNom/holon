import { useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter, FormGroup, InputGroup } from "@blueprintjs/core";
import { useRegisterSqlConnection, useBootstrapConfig } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SqlConnection } from "../../api/connectivity";
import { SECRET_REF_HELP } from "./shared";

export function SqlConnectionDialog({ editing, onClose }: { editing: SqlConnection | null; onClose: () => void }) {
  const isEditing = editing !== null;
  const { data: bootstrap } = useBootstrapConfig();
  const requireSecretRef = bootstrap?.require_connector_secret_ref === true;
  const [name, setName] = useState(editing?.name ?? "");
  const [host, setHost] = useState(editing?.host ?? "");
  const [port, setPort] = useState(editing != null ? String(editing.port) : "5432");
  const [database, setDatabase] = useState(editing?.database ?? "");
  const [username, setUsername] = useState(editing?.username ?? "");
  const [password, setPassword] = useState("");
  const [secretRef, setSecretRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const register = useRegisterSqlConnection();

  async function save() {
    setError(null);
    try {
      await register.mutateAsync({
        name,
        host,
        port: Number(port) || 5432,
        database,
        username,
        password: requireSecretRef ? undefined : password || undefined,
        secret_ref: secretRef || undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the SQL connection");
    }
  }

  const secretOk = isEditing || Boolean(secretRef) || (!requireSecretRef && Boolean(password));

  return (
    <Dialog isOpen title={isEditing ? "Edit SQL connection" : "New SQL connection"} onClose={onClose} style={{ width: 480 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Update host, database, or credentials — the name stays fixed since SQL sources already reference it."
            : "Postgres-wire databases (PostgreSQL, Redshift, CockroachDB). Register once, point several SQL sources at it."}
        </p>
        <FormGroup label="Name" helperText="e.g. erp_prod — referenced by SQL sources, not a dataset name">
          <InputGroup value={name} onChange={(e) => setName(e.target.value)} placeholder="my_db" disabled={isEditing} />
        </FormGroup>
        <FormGroup label="Host">
          <InputGroup value={host} onChange={(e) => setHost(e.target.value)} placeholder="db.example.com" />
        </FormGroup>
        <FormGroup label="Port">
          <InputGroup type="number" value={port} onChange={(e) => setPort(e.target.value)} placeholder="5432" />
        </FormGroup>
        <FormGroup label="Database">
          <InputGroup value={database} onChange={(e) => setDatabase(e.target.value)} placeholder="analytics" />
        </FormGroup>
        <FormGroup label="Username">
          <InputGroup value={username} onChange={(e) => setUsername(e.target.value)} placeholder="readonly_user" />
        </FormGroup>
        {!requireSecretRef && (
          <FormGroup
            label="Password"
            helperText={isEditing && editing?.has_password ? "A password is already set — leave blank to keep it." : undefined}
          >
            <InputGroup
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={isEditing && editing?.has_password ? "•••••••• (unchanged)" : "••••••••"}
            />
          </FormGroup>
        )}
        <FormGroup label="Secret reference" labelFor="sql-connection-secret-ref" helperText={SECRET_REF_HELP}>
          <InputGroup
            id="sql-connection-secret-ref"
            value={secretRef}
            onChange={(e) => setSecretRef(e.target.value)}
            placeholder="env:ERP_PASSWORD"
          />
        </FormGroup>
        {error && (
          <Callout intent="danger" className="hl-mt-sm" title="Couldn't save">
            {error}
          </Callout>
        )}
      </DialogBody>
      <DialogFooter
        actions={
          <>
            <Button onClick={onClose} disabled={register.isPending}>
              Cancel
            </Button>
            <Button
              intent="primary"
              loading={register.isPending}
              disabled={!name || !host || !database || !username || !secretOk}
              onClick={() => void save()}
            >
              Save
            </Button>
          </>
        }
      />
    </Dialog>
  );
}
