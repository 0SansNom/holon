import { useState } from "react";
import { Button, Callout, Checkbox, Dialog, DialogBody, DialogFooter, FormGroup, HTMLSelect, InputGroup } from "@blueprintjs/core";
import { useRegisterObjectConnection, useBootstrapConfig } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { ObjectConnection, ObjectConnectionKind } from "../../api/connectivity";
import { SECRET_REF_HELP } from "./shared";

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

  async function save() {
    setError(null);
    try {
      await register.mutateAsync({
        name,
        kind,
        endpoint: isAzure && !endpoint ? undefined : endpoint,
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

  return (
    <Dialog
      isOpen
      title={isEditing ? "Edit object storage connection" : "New object storage connection"}
      onClose={onClose}
      style={{ width: 480 }}
    >
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Update credentials — the name and kind stay fixed since object sources already reference this connection."
            : "S3-compatible storage (MinIO, AWS S3) or Azure Blob Storage. Register once, point several sources at it."}
        </p>
        <FormGroup label="Name" helperText="e.g. minio_prod — referenced by object sources, not a dataset name">
          <InputGroup value={name} onChange={(e) => setName(e.target.value)} placeholder="my_bucket_store" disabled={isEditing} />
        </FormGroup>
        <FormGroup label="Kind">
          <HTMLSelect
            value={kind}
            onChange={(e) => setKind(e.target.value as ObjectConnectionKind)}
            disabled={isEditing}
            options={[
              { value: "s3", label: "S3-compatible" },
              { value: "azure", label: "Azure Blob Storage" },
            ]}
          />
        </FormGroup>
        {isAzure ? (
          <FormGroup label="Storage account name">
            <InputGroup value={accessKeyId} onChange={(e) => setAccessKeyId(e.target.value)} placeholder="mystorageaccount" />
          </FormGroup>
        ) : (
          <>
            <FormGroup label="Endpoint" helperText='e.g. http://localhost:9000 or https://s3.amazonaws.com'>
              <InputGroup value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="http://localhost:9000" />
            </FormGroup>
            <FormGroup label="Region">
              <InputGroup value={region} onChange={(e) => setRegion(e.target.value)} placeholder="us-east-1" />
            </FormGroup>
            <FormGroup label="Access key ID">
              <InputGroup value={accessKeyId} onChange={(e) => setAccessKeyId(e.target.value)} placeholder="minioadmin" />
            </FormGroup>
          </>
        )}
        {!requireSecretRef && (
          <FormGroup
            label={isAzure ? "Account key" : "Secret access key"}
            helperText={
              isEditing && editing?.has_secret_access_key ? "A secret is already set — leave blank to keep it." : undefined
            }
          >
            <InputGroup
              type="password"
              value={secretAccessKey}
              onChange={(e) => setSecretAccessKey(e.target.value)}
              placeholder={isEditing && editing?.has_secret_access_key ? "•••••••• (unchanged)" : "••••••••"}
            />
          </FormGroup>
        )}
        <FormGroup label="Secret reference" labelFor="object-connection-secret-ref" helperText={SECRET_REF_HELP}>
          <InputGroup
            id="object-connection-secret-ref"
            value={secretRef}
            onChange={(e) => setSecretRef(e.target.value)}
            placeholder={isAzure ? "env:AZURE_STORAGE_KEY" : "env:MINIO_SECRET_KEY"}
          />
        </FormGroup>
        {!isAzure && (
          <FormGroup>
            <Checkbox
              checked={pathStyle}
              label="Path-style addressing"
              onChange={(e) => setPathStyle((e.target as HTMLInputElement).checked)}
            />
            <p className="hl-text-muted-sm hl-mt-xs">Enable for MinIO and most self-hosted S3 — disable for AWS virtual-hosted buckets.</p>
          </FormGroup>
        )}
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
              disabled={!name || (!isAzure && !endpoint) || !accessKeyId || !secretOk}
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
