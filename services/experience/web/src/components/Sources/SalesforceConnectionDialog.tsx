import { useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter } from "@blueprintjs/core";
import { useRegisterSalesforceConnection, useBootstrapConfig } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SalesforceConnection } from "../../api/connectivity";
import { ConnectionFields, type ConnectionField } from "./ConnectionFields";

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
  const fields: ConnectionField[] = [
    {
      kind: "text",
      id: "sf-connection-name",
      label: "Name",
      helperText: "e.g. sf_prod — referenced by Salesforce sources",
      value: name,
      onChange: setName,
      placeholder: "sf_prod",
      disabled: isEditing,
    },
    {
      kind: "text",
      id: "sf-connection-login-url",
      label: "Login URL",
      helperText: "Production, sandbox (test.salesforce.com), or My Domain",
      value: loginUrl,
      onChange: setLoginUrl,
      placeholder: "https://login.salesforce.com",
    },
    {
      kind: "text",
      id: "sf-connection-client-id",
      label: "Consumer Key (Client ID)",
      value: clientId,
      onChange: setClientId,
      placeholder: "3MVG9...",
    },
    ...(!requireSecretRef
      ? [
          {
            kind: "secret" as const,
            id: "sf-connection-client-secret",
            label: "Consumer Secret",
            helperText:
              isEditing && editing?.has_client_secret ? "A secret is already set — leave blank to keep it." : undefined,
            value: clientSecret,
            onChange: setClientSecret,
            placeholder: isEditing && editing?.has_client_secret ? "•••••••• (unchanged)" : "••••••••",
          },
        ]
      : []),
    {
      kind: "secretRef",
      id: "sf-connection-secret-ref",
      value: secretRef,
      onChange: setSecretRef,
      placeholder: "env:HOLON_CONN_<TENANT>__SF_CLIENT_SECRET",
    },
  ];

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
