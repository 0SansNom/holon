import { useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter, FormGroup, InputGroup } from "@blueprintjs/core";
import { useRegisterSalesforceConnection, useBootstrapConfig } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SalesforceConnection } from "../../api/connectivity";
import { SECRET_REF_HELP } from "./shared";

export function SalesforceConnectionDialog({
  editing,
  onClose,
}: {
  editing: SalesforceConnection | null;
  onClose: () => void;
}) {
  const isEditing = editing !== null;
  const { data: bootstrap } = useBootstrapConfig();
  const requireSecretRef = bootstrap?.require_connector_secret_ref === true;
  const [name, setName] = useState(editing?.name ?? "");
  const [loginUrl, setLoginUrl] = useState(editing?.login_url ?? "https://login.salesforce.com");
  const [clientId, setClientId] = useState(editing?.client_id ?? "");
  const [clientSecret, setClientSecret] = useState("");
  const [secretRef, setSecretRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const register = useRegisterSalesforceConnection();

  async function save() {
    setError(null);
    try {
      await register.mutateAsync({
        name,
        login_url: loginUrl || undefined,
        client_id: clientId,
        client_secret: requireSecretRef ? undefined : clientSecret || undefined,
        secret_ref: secretRef || undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the Salesforce connection");
    }
  }

  const secretOk = isEditing || Boolean(secretRef) || (!requireSecretRef && Boolean(clientSecret));

  return (
    <Dialog
      isOpen
      title={isEditing ? "Edit Salesforce connection" : "New Salesforce connection"}
      onClose={onClose}
      style={{ width: 520 }}
    >
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Update login URL or Connected App credentials — the name stays fixed since sources already reference it."
            : "Connected App client credentials. Register once, point several SOQL sources at it."}
        </p>
        <FormGroup label="Name" helperText="e.g. sf_prod — referenced by Salesforce sources">
          <InputGroup value={name} onChange={(e) => setName(e.target.value)} placeholder="sf_prod" disabled={isEditing} />
        </FormGroup>
        <FormGroup label="Login URL" helperText="Production, sandbox (test.salesforce.com), or My Domain">
          <InputGroup
            value={loginUrl}
            onChange={(e) => setLoginUrl(e.target.value)}
            placeholder="https://login.salesforce.com"
          />
        </FormGroup>
        <FormGroup label="Consumer Key (Client ID)">
          <InputGroup value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="3MVG9..." />
        </FormGroup>
        {!requireSecretRef && (
          <FormGroup
            label="Consumer Secret"
            helperText={
              isEditing && editing?.has_client_secret ? "A secret is already set — leave blank to keep it." : undefined
            }
          >
            <InputGroup
              type="password"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              placeholder={isEditing && editing?.has_client_secret ? "•••••••• (unchanged)" : "••••••••"}
            />
          </FormGroup>
        )}
        <FormGroup label="Secret reference" labelFor="sf-connection-secret-ref" helperText={SECRET_REF_HELP}>
          <InputGroup
            id="sf-connection-secret-ref"
            value={secretRef}
            onChange={(e) => setSecretRef(e.target.value)}
            placeholder="env:SF_CLIENT_SECRET"
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
              disabled={!name || !clientId || !secretOk}
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
