import { useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter } from "@blueprintjs/core";
import { useRegisterSftpConnection, useBootstrapConfig } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SftpConnection } from "../../api/connectivity";
import { ConnectionFields, type ConnectionField } from "./ConnectionFields";

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
  const fields: ConnectionField[] = [
    {
      kind: "text",
      id: "sftp-connection-name",
      label: "Name",
      value: name,
      onChange: setName,
      placeholder: "erp_files",
      helperText: "e.g. erp_files — referenced by SFTP sources",
      disabled: isEditing,
    },
    {
      kind: "text",
      id: "sftp-connection-host",
      label: "Host",
      value: host,
      onChange: setHost,
      placeholder: "sftp.example.com",
    },
    {
      kind: "number",
      id: "sftp-connection-port",
      label: "Port",
      value: port,
      onChange: setPort,
      placeholder: "22",
    },
    {
      kind: "text",
      id: "sftp-connection-username",
      label: "Username",
      value: username,
      onChange: setUsername,
      placeholder: "readonly",
    },
    ...(!requireSecretRef
      ? [
          {
            kind: "secret" as const,
            id: "sftp-connection-password",
            label: "Password",
            value: password,
            onChange: setPassword,
            placeholder: isEditing && editing?.has_password ? "•••••••• (unchanged)" : "••••••••",
            helperText: isEditing && editing?.has_password ? "A password is already set — leave blank to keep it." : undefined,
          },
        ]
      : []),
    {
      kind: "secretRef",
      id: "sftp-connection-secret-ref",
      value: secretRef,
      onChange: setSecretRef,
      placeholder: "env:HOLON_CONN_<TENANT>__SFTP_PASSWORD",
    },
  ];

  return (
    <Dialog isOpen title={isEditing ? "Edit SFTP connection" : "New SFTP connection"} onClose={onClose} style={{ width: 480 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Update host or credentials — the name stays fixed since SFTP sources already reference it."
            : "Password-authenticated SFTP. Register once, point several SFTP sources at it."}
        </p>
        <ConnectionFields fields={fields} />
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
