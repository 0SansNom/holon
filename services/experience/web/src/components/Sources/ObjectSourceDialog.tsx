import { useState } from "react";
import {
  Button,
  Callout,
  Checkbox,
  Dialog,
  DialogBody,
  DialogFooter,
  FormGroup,
  HTMLSelect,
  Icon,
  InputGroup,
} from "@blueprintjs/core";
import { useRegisterObjectSource, useSyncDataset, useObjectConnections } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { ObjectSource } from "../../api/connectivity";

type ReadMode = "object_key" | "key_prefix";

function formFromSource(source: ObjectSource) {
  return {
    name: source.name,
    connectionName: source.connection_name,
    bucket: source.bucket,
    format: source.format,
    mode: (source.object_key ? "object_key" : "key_prefix") as ReadMode,
    objectKey: source.object_key ?? "",
    keyPrefix: source.key_prefix ?? "",
    incremental: source.incremental,
    scheduleIntervalMinutes:
      source.schedule_interval_minutes != null ? String(source.schedule_interval_minutes) : "",
  };
}

const EMPTY = {
  name: "",
  connectionName: "",
  bucket: "",
  format: "csv",
  mode: "object_key" as ReadMode,
  objectKey: "",
  keyPrefix: "",
  incremental: false,
  scheduleIntervalMinutes: "",
};

export function ObjectSourceDialog({ editing, onClose }: { editing: ObjectSource | null; onClose: () => void }) {
  const isEditing = editing !== null;
  const [form, setForm] = useState(editing ? formFromSource(editing) : EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState<{ name: string; rowCount: number } | null>(null);

  const register = useRegisterObjectSource();
  const sync = useSyncDataset();
  const { data: connections } = useObjectConnections();

  const busy = register.isPending || sync.isPending;

  async function connectAndSync() {
    setError(null);
    try {
      await register.mutateAsync({
        name: form.name,
        connection_name: form.connectionName,
        bucket: form.bucket,
        format: form.format,
        object_key: form.mode === "object_key" ? form.objectKey || undefined : undefined,
        key_prefix: form.mode === "key_prefix" ? form.keyPrefix || undefined : undefined,
        incremental: form.mode === "key_prefix" ? form.incremental : false,
        schedule_interval_minutes: form.scheduleIntervalMinutes ? Number(form.scheduleIntervalMinutes) : undefined,
      });
      const result = await sync.mutateAsync(form.name);
      setConnected({ name: form.name, rowCount: result.row_count });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Connection failed");
    }
  }

  if (connected) {
    return (
      <Dialog isOpen title={isEditing ? "Edit object source" : "Connect object source"} onClose={onClose} style={{ width: 480 }}>
        <DialogBody>
          <div className="hl-dialog-success">
            <Icon icon="tick-circle" size={36} intent="success" />
            <p className="hl-dialog-success-title">{isEditing ? "Saved" : "Connected"}</p>
            <p className="hl-dialog-success-text">
              Synced {connected.rowCount} record{connected.rowCount === 1 ? "" : "s"} from{" "}
              <span className="hl-mono">{connected.name}</span>.
            </p>
          </div>
          <Callout intent="primary" icon="info-sign" className="hl-mt-sm">
            Next step: map <span className="hl-mono">{connected.name}</span> to an Object Type under Admin.
          </Callout>
        </DialogBody>
        <DialogFooter actions={<Button intent="primary" onClick={onClose}>Done</Button>} />
      </Dialog>
    );
  }

  return (
    <Dialog isOpen title={isEditing ? "Edit object source" : "Connect object source"} onClose={onClose} style={{ width: 520 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Change bucket, path, or schedule — the name stays fixed since sync calls already target it."
            : "Import CSV, NDJSON, or Parquet from S3-compatible storage — no code, nothing to deploy."}
        </p>
        <FormGroup label="Name" helperText="Dataset name — lowercase, no spaces (e.g. suppliers_csv)">
          <InputGroup
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            placeholder="landing_suppliers"
            disabled={isEditing}
          />
        </FormGroup>
        <FormGroup
          label="Object connection"
          helperText={
            connections && connections.length === 0
              ? "No object connections yet — add one from the Connections tab first."
              : undefined
          }
        >
          <HTMLSelect
            fill
            value={form.connectionName}
            onChange={(e) => setForm((f) => ({ ...f, connectionName: e.target.value }))}
          >
            <option value="">Select a connection…</option>
            {connections?.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} ({c.endpoint})
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Bucket">
          <InputGroup
            value={form.bucket}
            onChange={(e) => setForm((f) => ({ ...f, bucket: e.target.value }))}
            placeholder="holon-warehouse"
          />
        </FormGroup>
        <FormGroup label="Format">
          <HTMLSelect fill value={form.format} onChange={(e) => setForm((f) => ({ ...f, format: e.target.value }))}>
            <option value="csv">CSV</option>
            <option value="ndjson">NDJSON</option>
            <option value="parquet">Parquet</option>
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Read mode">
          <HTMLSelect
            fill
            value={form.mode}
            onChange={(e) => setForm((f) => ({ ...f, mode: e.target.value as ReadMode }))}
          >
            <option value="object_key">Single object</option>
            <option value="key_prefix">Prefix (all files under path)</option>
          </HTMLSelect>
        </FormGroup>
        {form.mode === "object_key" ? (
          <FormGroup label="Object key" helperText='Path inside the bucket, e.g. landing/suppliers.csv'>
            <InputGroup
              value={form.objectKey}
              onChange={(e) => setForm((f) => ({ ...f, objectKey: e.target.value }))}
              placeholder="landing/suppliers.csv"
            />
          </FormGroup>
        ) : (
          <>
            <FormGroup label="Key prefix" helperText='Folder prefix, e.g. landing/inbound/ — reads all files recursively'>
              <InputGroup
                value={form.keyPrefix}
                onChange={(e) => setForm((f) => ({ ...f, keyPrefix: e.target.value }))}
                placeholder="landing/inbound/"
              />
            </FormGroup>
            <FormGroup>
              <Checkbox
                checked={form.incremental}
                label="Incremental (only new keys since last sync)"
                onChange={(e) => setForm((f) => ({ ...f, incremental: (e.target as HTMLInputElement).checked }))}
              />
            </FormGroup>
          </>
        )}
        <FormGroup label="Sync every ___ minutes (optional)" helperText="Leave blank for manual-only syncs.">
          <InputGroup
            type="number"
            min={1}
            value={form.scheduleIntervalMinutes}
            onChange={(e) => setForm((f) => ({ ...f, scheduleIntervalMinutes: e.target.value }))}
            placeholder="60"
          />
        </FormGroup>
        {error && (
          <Callout intent="danger" className="hl-mt-sm" title="Couldn't connect">
            {error}
          </Callout>
        )}
      </DialogBody>
      <DialogFooter
        actions={
          <>
            <Button onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button
              intent="primary"
              loading={busy}
              disabled={
                !form.name ||
                !form.connectionName ||
                !form.bucket ||
                !form.format ||
                (form.mode === "object_key" ? !form.objectKey : !form.keyPrefix)
              }
              onClick={() => void connectAndSync()}
            >
              {isEditing ? "Save & sync" : "Connect & sync"}
            </Button>
          </>
        }
      />
    </Dialog>
  );
}
