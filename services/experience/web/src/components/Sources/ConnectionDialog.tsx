import { useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter, FormGroup, InputGroup } from "@blueprintjs/core";
import { useRegisterConnection, useBootstrapConfig } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { GenericConnection } from "../../api/connectivity";
import { SECRET_REF_HELP } from "./shared";

export function ConnectionDialog({ editing, onClose }: { editing: GenericConnection | null; onClose: () => void }) {
  const isEditing = editing !== null;
  const { data: bootstrap } = useBootstrapConfig();
  const requireSecretRef = bootstrap?.require_connector_secret_ref === true;
  const [name, setName] = useState(editing?.name ?? "");
  const [authHeaderName, setAuthHeaderName] = useState(editing?.auth_header_name ?? "");
  const [authHeaderValue, setAuthHeaderValue] = useState("");
  const [secretRef, setSecretRef] = useState("");
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
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the connection");
    }
  }

  const secretOk = isEditing || Boolean(secretRef) || (!requireSecretRef && Boolean(authHeaderValue));

  return (
    <Dialog isOpen title={isEditing ? "Edit connection" : "New connection"} onClose={onClose} style={{ width: 440 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Rotate the auth header — the name stays fixed since it's what every source pointed at this connection already references."
            : "A reusable credential — point as many sources at this as you like without re-entering the secret each time."}
        </p>
        <FormGroup label="Name" helperText="e.g. hubspot_prod — just a label, not the dataset name of any one source">
          <InputGroup
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="my_api_credential"
            disabled={isEditing}
          />
        </FormGroup>
        <FormGroup label="Auth header name" helperText='e.g. "Authorization" or "X-API-Key"'>
          <InputGroup value={authHeaderName} onChange={(e) => setAuthHeaderName(e.target.value)} placeholder="Authorization" />
        </FormGroup>
        {!requireSecretRef && (
          <FormGroup
            label="Auth header value"
            helperText={isEditing ? "A value is already set — leave blank to keep it." : undefined}
          >
            <InputGroup
              type="password"
              value={authHeaderValue}
              onChange={(e) => setAuthHeaderValue(e.target.value)}
              placeholder={isEditing ? "•••••••• (unchanged)" : "Bearer sk_live_..."}
            />
          </FormGroup>
        )}
        <FormGroup label="Secret reference" labelFor="connection-secret-ref" helperText={SECRET_REF_HELP}>
          <InputGroup
            id="connection-secret-ref"
            value={secretRef}
            onChange={(e) => setSecretRef(e.target.value)}
            placeholder="env:HUBSPOT_TOKEN"
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
              disabled={!name || !authHeaderName || !secretOk}
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
