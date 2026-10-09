import { useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter } from "@blueprintjs/core";
import { useRegisterObjectConnection, useBootstrapConfig } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { ObjectConnection, ObjectConnectionKind } from "../../api/connectivity";
import { ConnectionFields, type ConnectionField } from "./ConnectionFields";

export function ObjectConnectionDialog({ editing, onClose }: { editing: ObjectConnection | null; onClose: () => void }) {
  const isEditing = editing !== null;
  const { data: bootstrap } = useBootstrapConfig();
  const requireSecretRef = bootstrap?.require_connector_secret_ref === true;
  const [name, setName] = useState(editing?.name ?? "");
  const [kind, setKind] = useState<ObjectConnectionKind>(editing?.kind ?? "s3");
  const [endpoint, setEndpoint] = useState(editing?.endpoint ?? "");
  const [region, setRegion] = useState(editing?.region ?? "us-east-1");
  const [accessKeyId, setAccessKeyId] = useState(editing?.access_key_id ?? "");
  const [secretAccessKey, setSecretAccessKey] = useState("");
  const [secretRef, setSecretRef] = useState("");
  const [pathStyle, setPathStyle] = useState(editing?.path_style ?? true);
  const [error, setError] = useState<string | null>(null);
  const register = useRegisterObjectConnection();
  const isAzure = kind === "azure";
  const isGcs = kind === "gcs";

  function onKindChange(next: ObjectConnectionKind) {
    setKind(next);
    if (next === "gcs" && (region === "us-east-1" || !region)) setRegion("US");
    if (next === "s3" && region === "US") setRegion("us-east-1");
    if (next === "gcs") setPathStyle(false);
    if (next === "s3") setPathStyle(true);
  }

  async function save() {
    setError(null);
    try {
      await register.mutateAsync({
        name,
        kind,
        endpoint: (isAzure || isGcs) && !endpoint ? undefined : endpoint,
        region,
        access_key_id: accessKeyId,
        path_style: pathStyle,
        secret_access_key: requireSecretRef ? undefined : secretAccessKey || undefined,
        secret_ref: secretRef || undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the object connection");
    }
  }

  const secretOk = isEditing || Boolean(secretRef) || (!requireSecretRef && Boolean(secretAccessKey));
  const endpointRequired = kind === "s3";
  const secretPlaceholder =
    isEditing && editing?.has_secret_access_key
      ? isGcs
        ? "(unchanged — paste new JSON to replace)"
        : "•••••••• (unchanged)"
      : isGcs
        ? '{\n  "type": "service_account",\n  ...\n}'
        : "••••••••";
  const secretHelper =
    isEditing && editing?.has_secret_access_key
      ? "A secret is already set — leave blank to keep it."
      : isGcs
        ? "Paste the Google service account key JSON (OAuthJWTCertType=GOOGLEJSON)."
        : undefined;
  const kindFields: ConnectionField[] = isAzure
    ? [
        {
          kind: "text",
          id: "object-connection-account",
          label: "Storage account name",
          value: accessKeyId,
          onChange: setAccessKeyId,
          placeholder: "mystorageaccount",
        },
      ]
    : isGcs
      ? [
          {
            kind: "text",
            id: "object-connection-project",
            label: "Project ID",
            value: accessKeyId,
            onChange: setAccessKeyId,
            placeholder: "my-gcp-project",
            helperText: "GCP Project Id containing the bucket (CData ProjectId)",
          },
          {
            kind: "text",
            id: "object-connection-location",
            label: "Default bucket location",
            value: region,
            onChange: setRegion,
            placeholder: "US",
            helperText: "e.g. US, EU, ASIA — used when creating objects",
          },
          {
            kind: "text",
            id: "object-connection-endpoint",
            label: "Endpoint",
            value: endpoint,
            onChange: setEndpoint,
            placeholder: "https://storage.googleapis.com",
            helperText: "Optional — defaults to https://storage.googleapis.com",
          },
        ]
      : [
          {
            kind: "text",
            id: "object-connection-endpoint",
            label: "Endpoint",
            value: endpoint,
            onChange: setEndpoint,
            placeholder: "https://s3.us-south.cloud-object-storage.appdomain.cloud",
            helperText:
              "e.g. http://localhost:9000, https://s3.amazonaws.com, or https://s3.us-south.cloud-object-storage.appdomain.cloud (IBM COS)",
          },
          {
            kind: "text",
            id: "object-connection-region",
            label: "Region",
            value: region,
            onChange: setRegion,
            placeholder: "us-south",
            helperText: "Match the endpoint region for IBM COS (e.g. us-south, eu-de).",
          },
          {
            kind: "text",
            id: "object-connection-access-key",
            label: "Access key ID",
            value: accessKeyId,
            onChange: setAccessKeyId,
            placeholder: "minioadmin",
            helperText: "HMAC access key for AWS / MinIO / IBM COS",
          },
        ];
  const fields: ConnectionField[] = [
    {
      kind: "text",
      id: "object-connection-name",
      label: "Name",
      value: name,
      onChange: setName,
      placeholder: "my_bucket_store",
      helperText: "e.g. gcs_prod — referenced by object sources, not a dataset name",
      disabled: isEditing,
    },
    {
      kind: "select",
      id: "object-connection-kind",
      label: "Kind",
      value: kind,
      onChange: (value) => onKindChange(value as ObjectConnectionKind),
      disabled: isEditing,
      fill: false,
      options: [
        { value: "s3", label: "S3-compatible" },
        { value: "azure", label: "Azure Blob Storage" },
        { value: "gcs", label: "Google Cloud Storage" },
      ],
    },
    ...kindFields,
    ...(!requireSecretRef
      ? [
          isGcs
            ? {
                kind: "textarea" as const,
                id: "object-connection-secret",
                label: "Service account JSON",
                value: secretAccessKey,
                onChange: setSecretAccessKey,
                placeholder: secretPlaceholder,
                helperText: secretHelper,
                rows: 6,
                mono: true,
              }
            : {
                kind: "secret" as const,
                id: "object-connection-secret",
                label: isAzure ? "Account key" : "Secret access key",
                value: secretAccessKey,
                onChange: setSecretAccessKey,
                placeholder: secretPlaceholder,
                helperText: secretHelper,
              },
        ]
      : []),
    {
      kind: "secretRef",
      id: "object-connection-secret-ref",
      value: secretRef,
      onChange: setSecretRef,
      placeholder: isAzure
        ? "env:HOLON_CONN_<TENANT>__AZURE_STORAGE_KEY"
        : isGcs
          ? "env:HOLON_CONN_<TENANT>__GCS_SERVICE_ACCOUNT_JSON"
          : "env:HOLON_CONN_<TENANT>__S3_SECRET_KEY",
    },
    ...(kind === "s3"
      ? [
          {
            kind: "checkbox" as const,
            id: "object-connection-path-style",
            label: "Path-style addressing",
            checked: pathStyle,
            onChange: setPathStyle,
            helperText: "Enable for MinIO and most self-hosted S3 — disable for AWS and IBM COS virtual-hosted buckets.",
          },
        ]
      : []),
  ];

  return (
    <Dialog
      isOpen
      title={isEditing ? "Edit object storage connection" : "New object storage connection"}
      onClose={onClose}
      style={{ width: 520 }}
    >
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Update credentials — the name and kind stay fixed since object sources already reference this connection."
            : "S3-compatible (MinIO, AWS S3, IBM COS), Azure Blob, or Google Cloud Storage. Register once, point several sources at it."}
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
              disabled={!name || (endpointRequired && !endpoint) || !accessKeyId || !secretOk}
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
