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
import { useRegisterSftpSource, useSyncDataset, useSftpConnections } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SftpSource } from "../../api/connectivity";

type ReadMode = "remote_path" | "remote_prefix";

function formFromSource(source: SftpSource) {
  return {
    name: source.name,
    connectionName: source.connection_name,
    format: source.format,
    mode: (source.remote_path ? "remote_path" : "remote_prefix") as ReadMode,
    remotePath: source.remote_path ?? "",
    remotePrefix: source.remote_prefix ?? "",
    incremental: source.incremental,
    scheduleIntervalMinutes:
      source.schedule_interval_minutes != null ? String(source.schedule_interval_minutes) : "",
  };
}

const EMPTY = {
  name: "",
  connectionName: "",
  format: "csv",
  mode: "remote_path" as ReadMode,
  remotePath: "",
  remotePrefix: "",
  incremental: false,
  scheduleIntervalMinutes: "",
};

export function SftpSourceDialog({ editing, onClose }: { editing: SftpSource | null; onClose: () => void }) {
  const isEditing = editing !== null;
  const [form, setForm] = useState(editing ? formFromSource(editing) : EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState<{ name: string; rowCount: number } | null>(null);
  const register = useRegisterSftpSource();
  const sync = useSyncDataset();
  const { data: connections } = useSftpConnections();

  const busy = register.isPending || sync.isPending;

  async function connectAndSync() {
    setError(null);
    try {
      await register.mutateAsync({
        name: form.name,
        connection_name: form.connectionName,
        format: form.format,
        remote_path: form.mode === "remote_path" ? form.remotePath || undefined : undefined,
        remote_prefix: form.mode === "remote_prefix" ? form.remotePrefix || undefined : undefined,
        incremental: form.mode === "remote_prefix" ? form.incremental : false,
        schedule_interval_minutes: form.scheduleIntervalMinutes
          ? Number(form.scheduleIntervalMinutes)
          : undefined,
      });
      const result = await sync.mutateAsync(form.name);
      setConnected({ name: form.name, rowCount: result.row_count });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Connection failed");
    }
  }

  if (connected) {
    return (
      <Dialog isOpen title={isEditing ? "Edit SFTP source" : "Connect SFTP"} onClose={onClose} style={{ width: 480 }}>
        <DialogBody>
          <div className="hl-dialog-success">
            <Icon icon="tick-circle" size={36} intent="success" />
            <p className="hl-dialog-success-title">{isEditing ? "Saved" : "Connected"}</p>
            <p className="hl-dialog-success-text">
              Synced {connected.rowCount} record{connected.rowCount === 1 ? "" : "s"} from{" "}
              <span className="hl-mono">{connected.name}</span>.
            </p>
          </div>
        </DialogBody>
        <DialogFooter
          actions={
            <Button intent="primary" onClick={onClose}>
              Done
            </Button>
          }
        />
      </Dialog>
    );
  }

  const pathOk =
    form.mode === "remote_path" ? Boolean(form.remotePath) : Boolean(form.remotePrefix);

  return (
    <Dialog isOpen title={isEditing ? "Edit SFTP source" : "Connect SFTP"} onClose={onClose} style={{ width: 520 }}>
      <DialogBody>
        <p className="hl-dialog-desc">Import CSV, NDJSON, or Parquet from a remote file or directory prefix.</p>
        <FormGroup label="Dataset name">
          <InputGroup
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="sftp_suppliers"
            disabled={isEditing}
          />
        </FormGroup>
        <FormGroup label="Connection">
          <HTMLSelect
            fill
            value={form.connectionName}
            onChange={(e) => setForm({ ...form, connectionName: e.target.value })}
          >
            <option value="">Select…</option>
            {(connections ?? []).map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} ({c.host})
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Format">
          <HTMLSelect fill value={form.format} onChange={(e) => setForm({ ...form, format: e.target.value })}>
            <option value="csv">CSV</option>
            <option value="ndjson">NDJSON</option>
            <option value="parquet">Parquet</option>
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Read mode">
          <HTMLSelect
            fill
            value={form.mode}
            onChange={(e) => setForm({ ...form, mode: e.target.value as ReadMode, incremental: false })}
          >
            <option value="remote_path">Single file</option>
            <option value="remote_prefix">Directory / prefix</option>
          </HTMLSelect>
        </FormGroup>
        {form.mode === "remote_path" ? (
          <FormGroup label="Remote path" helperText="e.g. upload/landing/suppliers.csv">
            <InputGroup
              value={form.remotePath}
              onChange={(e) => setForm({ ...form, remotePath: e.target.value })}
              placeholder="upload/data.csv"
            />
          </FormGroup>
        ) : (
          <>
            <FormGroup label="Remote prefix" helperText="Directory or filename prefix under the SFTP home">
              <InputGroup
                value={form.remotePrefix}
                onChange={(e) => setForm({ ...form, remotePrefix: e.target.value })}
                placeholder="upload/landing"
              />
            </FormGroup>
            <Checkbox
              checked={form.incremental}
              label="Incremental (only paths after last sync)"
              onChange={(e) => setForm({ ...form, incremental: e.currentTarget.checked })}
            />
          </>
        )}
        <FormGroup label="Schedule (minutes)" helperText="Optional — leave blank for manual sync only">
          <InputGroup
            type="number"
            value={form.scheduleIntervalMinutes}
            onChange={(e) => setForm({ ...form, scheduleIntervalMinutes: e.target.value })}
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
              disabled={!form.name || !form.connectionName || !pathOk}
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
