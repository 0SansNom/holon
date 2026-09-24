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
import { useRegisterSalesforceSource, useSyncDataset, useSalesforceConnections } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SalesforceSource } from "../../api/connectivity";

function formFromSource(source: SalesforceSource) {
  return {
    name: source.name,
    connectionName: source.connection_name,
    soql: source.soql,
    apiVersion: source.api_version,
    cursorProperty: source.cursor_property ?? "",
    scheduleIntervalMinutes:
      source.schedule_interval_minutes != null ? String(source.schedule_interval_minutes) : "",
  };
}

const EMPTY = {
  name: "",
  connectionName: "",
  soql: "SELECT Id, Name, SystemModstamp FROM Account",
  apiVersion: "v59.0",
  cursorProperty: "",
  scheduleIntervalMinutes: "",
};

export function SalesforceSourceDialog({ editing, onClose }: { editing: SalesforceSource | null; onClose: () => void }) {
  const isEditing = editing !== null;
  const [form, setForm] = useState(editing ? formFromSource(editing) : EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState<{ name: string; rowCount: number } | null>(null);
  const register = useRegisterSalesforceSource();
  const sync = useSyncDataset();
  const { data: connections } = useSalesforceConnections();

  const busy = register.isPending || sync.isPending;

  async function connectAndSync() {
    setError(null);
    try {
      await register.mutateAsync({
        name: form.name,
        connection_name: form.connectionName,
        soql: form.soql,
        api_version: form.apiVersion || undefined,
        cursor_property: form.cursorProperty || undefined,
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
      <Dialog
        isOpen
        title={isEditing ? "Edit Salesforce source" : "Connect Salesforce"}
        onClose={onClose}
        style={{ width: 480 }}
      >
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

  return (
    <Dialog
      isOpen
      title={isEditing ? "Edit Salesforce source" : "Connect Salesforce"}
      onClose={onClose}
      style={{ width: 560 }}
    >
      <DialogBody>
        <p className="hl-dialog-desc">Run a SOQL query against a registered Salesforce connection.</p>
        <FormGroup label="Dataset name">
          <InputGroup
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="sf_accounts"
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
                {c.name}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="SOQL" helperText="Must be a SELECT. Pagination follows nextRecordsUrl automatically.">
          <TextArea
            fill
            rows={5}
            value={form.soql}
            onChange={(e) => setForm({ ...form, soql: e.target.value })}
            style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 12 }}
          />
        </FormGroup>
        <FormGroup label="API version">
          <InputGroup
            value={form.apiVersion}
            onChange={(e) => setForm({ ...form, apiVersion: e.target.value })}
            placeholder="v59.0"
          />
        </FormGroup>
        <FormGroup
          label="Cursor property (optional)"
          helperText="e.g. SystemModstamp — enables incremental append syncs"
        >
          <InputGroup
            value={form.cursorProperty}
            onChange={(e) => setForm({ ...form, cursorProperty: e.target.value })}
            placeholder="SystemModstamp"
          />
        </FormGroup>
        <FormGroup label="Schedule interval (minutes)" helperText="Leave blank for manual sync only">
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
              disabled={!form.name || !form.connectionName || !form.soql}
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
