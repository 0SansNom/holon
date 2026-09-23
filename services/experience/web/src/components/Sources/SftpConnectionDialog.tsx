import { useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter, FormGroup, InputGroup } from "@blueprintjs/core";
import { useRegisterSftpConnection, useBootstrapConfig } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SftpConnection } from "../../api/connectivity";
import { SECRET_REF_HELP } from "./shared";

export function SftpConnectionDialog({ editing, onClose }: { editing: SftpConnection | null; onClose: () => void }) {
  const isEditing = editing !== null;
  const { data: bootstrap } = useBootstrapConfig();
  const requireSecretRef = bootstrap?.require_connector_secret_ref === true;
  const [name, setName] = useState(editing?.name ?? "");
  const [host, setHost] = useState(editing?.host ?? "");
  const [port, setPort] = useState(editing != null ? String(editing.port) : "22");
  const [username, setUsername] = useState(editing?.username ?? "");
  const [password, setPassword] = useState("");
  const [secretRef, setSecretRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const register = useRegisterSftpConnection();

  async function save() {
    setError(null);
    try {
      await register.mutateAsync({
        name,
        host,
        port: Number(port) || 22,
        username,
        password: requireSecretRef ? undefined : password || undefined,
        secret_ref: secretRef || undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the SFTP connection");
    }
  }

  const secretOk = isEditing || Boolean(secretRef) || (!requireSecretRef && Boolean(password));

  return (
    <Dialog isOpen title={isEditing ? "Edit SFTP connection" : "New SFTP connection"} onClose={onClose} style={{ width: 480 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Update host or credentials — the name stays fixed since SFTP sources already reference it."
            : "Password-authenticated SFTP. Register once, point several SFTP sources at it."}
        </p>
        <FormGroup label="Name" helperText="e.g. erp_files — referenced by SFTP sources">
          <InputGroup value={name} onChange={(e) => setName(e.target.value)} placeholder="erp_files" disabled={isEditing} />
        </FormGroup>
        <FormGroup label="Host">
          <InputGroup value={host} onChange={(e) => setHost(e.target.value)} placeholder="sftp.example.com" />
        </FormGroup>
        <FormGroup label="Port">
          <InputGroup type="number" value={port} onChange={(e) => setPort(e.target.value)} placeholder="22" />
        </FormGroup>
        <FormGroup label="Username">
          <InputGroup value={username} onChange={(e) => setUsername(e.target.value)} placeholder="readonly" />
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
        <FormGroup label="Secret reference" labelFor="sftp-connection-secret-ref" helperText={SECRET_REF_HELP}>
          <InputGroup
            id="sftp-connection-secret-ref"
            value={secretRef}
            onChange={(e) => setSecretRef(e.target.value)}
            placeholder="env:SFTP_PASSWORD"
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
              disabled={!name || !host || !username || !secretOk}
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
