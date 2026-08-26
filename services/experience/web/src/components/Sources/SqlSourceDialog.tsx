import { useState } from "react";
import {
  Button,
  Callout,
  Dialog,
  DialogBody,
  DialogFooter,
  FormGroup,
  HTMLSelect,
  Icon,
  InputGroup,
  TextArea,
} from "@blueprintjs/core";
import { useRegisterSqlSource, useSyncDataset, useSqlConnections } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SqlSource } from "../../api/connectivity";

type SourceMode = "table" | "query";

function formFromSource(source: SqlSource): {
  name: string;
  connectionName: string;
  mode: SourceMode;
  tableName: string;
  query: string;
  scheduleIntervalMinutes: string;
  cursorProperty: string;
} {
  return {
    name: source.name,
    connectionName: source.connection_name,
    mode: source.query ? "query" : "table",
    tableName: source.table_name ?? "",
    query: source.query ?? "",
    scheduleIntervalMinutes:
      source.schedule_interval_minutes != null ? String(source.schedule_interval_minutes) : "",
    cursorProperty: source.cursor_property ?? "",
  };
}

const EMPTY = {
  name: "",
  connectionName: "",
  mode: "table" as SourceMode,
  tableName: "",
  query: "",
  scheduleIntervalMinutes: "",
  cursorProperty: "",
};

export function SqlSourceDialog({ editing, onClose }: { editing: SqlSource | null; onClose: () => void }) {
  const isEditing = editing !== null;
  const [form, setForm] = useState(editing ? formFromSource(editing) : EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState<{ name: string; rowCount: number } | null>(null);

  const register = useRegisterSqlSource();
  const sync = useSyncDataset();
  const { data: connections } = useSqlConnections();

  const busy = register.isPending || sync.isPending;

  async function connectAndSync() {
    setError(null);
    try {
      await register.mutateAsync({
        name: form.name,
        connection_name: form.connectionName,
        table_name: form.mode === "table" ? form.tableName || undefined : undefined,
        query: form.mode === "query" ? form.query || undefined : undefined,
        schedule_interval_minutes: form.scheduleIntervalMinutes ? Number(form.scheduleIntervalMinutes) : undefined,
        cursor_property: form.cursorProperty || undefined,
      });
      const result = await sync.mutateAsync(form.name);
      setConnected({ name: form.name, rowCount: result.row_count });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Connection failed");
    }
  }

  if (connected) {
    return (
      <Dialog isOpen title={isEditing ? "Edit SQL source" : "Connect SQL source"} onClose={onClose} style={{ width: 480 }}>
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
    <Dialog isOpen title={isEditing ? "Edit SQL source" : "Connect SQL source"} onClose={onClose} style={{ width: 520 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Change the connection, table/query, or schedule — the name stays fixed since sync calls already target it."
            : "Read from a table or run a read-only SELECT — no code, nothing to deploy."}
        </p>
        <FormGroup label="Name" helperText="Dataset name — lowercase, no spaces (e.g. erp_orders)">
          <InputGroup
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            placeholder="my_table"
            disabled={isEditing}
          />
        </FormGroup>
        <FormGroup
          label="SQL connection"
          helperText={
            connections && connections.length === 0
              ? "No SQL connections yet — add one from the Connections tab first."
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
                {c.name} ({c.host}/{c.database})
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Read mode">
          <HTMLSelect
            fill
            value={form.mode}
            onChange={(e) => setForm((f) => ({ ...f, mode: e.target.value as SourceMode }))}
          >
            <option value="table">Whole table</option>
            <option value="query">Custom SELECT</option>
          </HTMLSelect>
        </FormGroup>
        {form.mode === "table" ? (
          <FormGroup label="Table name" helperText="Schema-qualified if needed, e.g. public.orders">
            <InputGroup
              value={form.tableName}
              onChange={(e) => setForm((f) => ({ ...f, tableName: e.target.value }))}
              placeholder="orders"
            />
          </FormGroup>
        ) : (
          <FormGroup label="Query" helperText="Read-only SELECT or WITH — no semicolons, no writes">
            <TextArea
              fill
              rows={5}
              value={form.query}
              onChange={(e) => setForm((f) => ({ ...f, query: e.target.value }))}
              placeholder="SELECT id, name, updated_at FROM orders WHERE status = 'active'"
            />
          </FormGroup>
        )}
        <FormGroup
          label="Sync every ___ minutes (optional)"
          helperText="Leave blank for manual-only syncs."
        >
          <InputGroup
            type="number"
            min={1}
            value={form.scheduleIntervalMinutes}
            onChange={(e) => setForm((f) => ({ ...f, scheduleIntervalMinutes: e.target.value }))}
            placeholder="60"
          />
        </FormGroup>
        <FormGroup
          label="Cursor column (optional)"
          helperText='Column watched for incremental sync (e.g. "updated_at"). Uses Iceberg append when set.'
        >
          <InputGroup
            value={form.cursorProperty}
            onChange={(e) => setForm((f) => ({ ...f, cursorProperty: e.target.value }))}
            placeholder="updated_at"
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
                (form.mode === "table" ? !form.tableName : !form.query.trim())
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
