import { useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter } from "@blueprintjs/core";
import { useRegisterConnection, useBootstrapConfig } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { GenericConnection } from "../../api/connectivity";
import { ConnectionFields, type ConnectionField } from "./ConnectionFields";

export function ConnectionDialog({ editing, onClose }: { editing: GenericConnection | null; onClose: () => void }) {
  const isEditing = editing !== null;
  const { data: bootstrap } = useBootstrapConfig();
  const requireSecretRef = bootstrap?.require_connector_secret_ref === true;
  const [name, setName] = useState(editing?.name ?? "");
  const [authHeaderName, setAuthHeaderName] = useState(editing?.auth_header_name ?? "");
  const [authHeaderValue, setAuthHeaderValue] = useState("");
  const [secretRef, setSecretRef] = useState("");
  const [allowedOrigin, setAllowedOrigin] = useState(editing?.allowed_origin ?? "");
  const [error, setError] = useState<string | null>(null);
  const register = useRegisterConnection();

  async function save() {
    setError(null);
    try {
      await register.mutateAsync({
        name,
        auth_header_name: authHeaderName,
        auth_header_value: requireSecretRef ? undefined : authHeaderValue || undefined,
        secret_ref: secretRef || undefined,
        allowed_origin: allowedOrigin || undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the connection");
    }
  }

  const secretOk = isEditing || Boolean(secretRef) || (!requireSecretRef && Boolean(authHeaderValue));
  const fields: ConnectionField[] = [
    {
      kind: "text",
      id: "connection-name",
      label: "Name",
      helperText: "e.g. hubspot_prod — just a label, not the dataset name of any one source",
      value: name,
      onChange: setName,
      placeholder: "my_api_credential",
      disabled: isEditing,
    },
    {
      kind: "text",
      id: "connection-allowed-origin",
      label: "Allowed origin",
      helperText:
        "The only origin sources using this connection may call, e.g. https://api.hubapi.com. Changing it requires re-entering the secret.",
      value: allowedOrigin,
      onChange: setAllowedOrigin,
      placeholder: "https://api.example.com",
    },
    {
      kind: "text",
      id: "connection-auth-header-name",
      label: "Auth header name",
      helperText: 'e.g. "Authorization" or "X-API-Key"',
      value: authHeaderName,
      onChange: setAuthHeaderName,
      placeholder: "Authorization",
    },
    ...(!requireSecretRef
      ? [
          {
            kind: "secret" as const,
            id: "connection-auth-header-value",
            label: "Auth header value",
            helperText: isEditing ? "A value is already set — leave blank to keep it." : undefined,
            value: authHeaderValue,
            onChange: setAuthHeaderValue,
            placeholder: isEditing ? "•••••••• (unchanged)" : "Bearer sk_live_...",
          },
        ]
      : []),
    {
      kind: "secretRef",
      id: "connection-secret-ref",
      value: secretRef,
      onChange: setSecretRef,
      placeholder: "env:HOLON_CONN_<TENANT>__HUBSPOT_TOKEN",
    },
  ];

  return (
    <Dialog isOpen title={isEditing ? "Edit connection" : "New connection"} onClose={onClose} style={{ width: 440 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Rotate the auth header — the name stays fixed since it's what every source pointed at this connection already references."
            : "A reusable credential — point as many sources at this as you like without re-entering the secret each time."}
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
              disabled={!name || !authHeaderName || !allowedOrigin || !secretOk}
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
