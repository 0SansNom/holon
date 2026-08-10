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
} from "@blueprintjs/core";
import { useRegisterSource, useSyncDataset, useConnections } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { GenericSource } from "../../api/connectivity";
import { type AuthMethod, EMPTY_FORM, formFromSource } from "./shared";

// Two distinct screens, never both on screen at once: the form (create
// or edit) and a success confirmation with exactly two unambiguous next
// actions. Also doubles as the edit flow — `editing` pre-fills
// everything except the secret, which the API never echoes back
// (`has_auth_header_value` only says whether one exists), so the value
// field starts blank with a "leave blank to keep it" hint rather than
// lying about there being nothing configured.
export function ConnectSourceDialog({ editing, onClose }: { editing: GenericSource | null; onClose: () => void }) {
  const [form, setForm] = useState(editing ? formFromSource(editing) : EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState<{ name: string; rowCount: number } | null>(null);

  const register = useRegisterSource();
  const sync = useSyncDataset();
  const { data: connections } = useConnections();

  const busy = register.isPending || sync.isPending;
  const isEditing = editing !== null;

  async function connectAndSync() {
    setError(null);
    try {
      await register.mutateAsync({
        name: form.name,
        base_url: form.baseUrl,
        connection_name: form.authMethod === "connection" ? form.connectionName || undefined : undefined,
        auth_header_name: form.authMethod === "inline" ? form.authHeaderName || undefined : undefined,
        auth_header_value: form.authMethod === "inline" ? form.authHeaderValue || undefined : undefined,
        record_path: form.recordPath || undefined,
        next_page_path: form.nextPagePath || undefined,
        schedule_interval_minutes: form.scheduleIntervalMinutes ? Number(form.scheduleIntervalMinutes) : undefined,
        cursor_property: form.cursorProperty || undefined,
        incremental_param: form.incrementalParam || undefined,
      });
      const result = await sync.mutateAsync(form.name);
      setConnected({ name: form.name, rowCount: result.row_count });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Connection failed");
    }
  }

  function connectAnother() {
    setForm(EMPTY_FORM);
    setError(null);
    setConnected(null);
  }

  if (connected) {
    return (
      <Dialog isOpen title={isEditing ? "Edit source" : "Connect a source"} onClose={onClose} style={{ width: 480 }}>
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
            Next step: mapping <span className="hl-mono">{connected.name}</span> to an Object Type, under Admin —
            it's catalogued but not yet browsable as objects.
          </Callout>
        </DialogBody>
        <DialogFooter
          actions={
            <>
              {!isEditing && <Button onClick={connectAnother}>Connect another source</Button>}
              <Button intent="primary" onClick={onClose}>
                Done
              </Button>
            </>
          }
        />
      </Dialog>
    );
  }

  return (
    <Dialog isOpen title={isEditing ? "Edit source" : "Connect a source"} onClose={onClose} style={{ width: 480 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Change the URL, auth, or record path — the name stays fixed since it's what every sync call already targets."
            : "Any REST API that returns JSON — no code, nothing to deploy. Paste the URL, add an auth header if the API needs one, and connect."}
        </p>
        <FormGroup label="Name" helperText="Used as the dataset name — lowercase, no spaces (e.g. hubspot_contacts)">
          <InputGroup
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            placeholder="my_source"
            disabled={isEditing}
          />
        </FormGroup>
        <FormGroup label="API URL">
          <InputGroup
            value={form.baseUrl}
            onChange={(e) => setForm((f) => ({ ...f, baseUrl: e.target.value }))}
            placeholder="https://api.example.com/records"
          />
        </FormGroup>
        <FormGroup label="Authentication">
          <HTMLSelect
            fill
            value={form.authMethod}
            onChange={(e) => setForm((f) => ({ ...f, authMethod: e.target.value as AuthMethod }))}
          >
            <option value="none">None</option>
            <option value="connection">Saved connection</option>
            <option value="inline">One-time header</option>
          </HTMLSelect>
        </FormGroup>
        {form.authMethod === "connection" && (
          <FormGroup
            label="Connection"
            helperText={
              connections && connections.length === 0
                ? "No connections saved yet — add one from the Connections section below."
                : "Reused across as many sources as you like — the secret is stored once."
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
                  {c.name} ({c.auth_header_name})
                </option>
              ))}
            </HTMLSelect>
          </FormGroup>
        )}
        {form.authMethod === "inline" && (
          <>
            <FormGroup label="Auth header name" helperText='e.g. "Authorization" or "X-API-Key"'>
              <InputGroup
                value={form.authHeaderName}
                onChange={(e) => setForm((f) => ({ ...f, authHeaderName: e.target.value }))}
                placeholder="Authorization"
              />
            </FormGroup>
            <FormGroup
              label="Auth header value"
              helperText={isEditing && editing?.has_auth_header_value ? "A value is already set — leave blank to keep it." : undefined}
            >
              <InputGroup
                type="password"
                value={form.authHeaderValue}
                onChange={(e) => setForm((f) => ({ ...f, authHeaderValue: e.target.value }))}
                placeholder={isEditing && editing?.has_auth_header_value ? "•••••••• (unchanged)" : "Bearer sk_live_..."}
              />
            </FormGroup>
          </>
        )}
        <FormGroup
          label="Record path (optional)"
          helperText='Only needed if the records are nested, e.g. "data.items" — leave blank if the response is already a list.'
        >
          <InputGroup
            value={form.recordPath}
            onChange={(e) => setForm((f) => ({ ...f, recordPath: e.target.value }))}
            placeholder="data.items"
          />
        </FormGroup>
        <FormGroup
          label="Next page path (optional)"
          helperText='For paginated APIs that return their own next-page link in the body, e.g. "next" — leave blank for a single-page response. Not every pagination style is supported (see the docs) — a source that stops before the end will show a clear error, not silently truncated data.'
        >
          <InputGroup
            value={form.nextPagePath}
            onChange={(e) => setForm((f) => ({ ...f, nextPagePath: e.target.value }))}
            placeholder="next"
          />
        </FormGroup>
        <FormGroup
          label="Sync every ___ minutes (optional)"
          helperText="Leave blank for manual-only. A background check runs about once a minute — a source is synced as soon as it's actually due, not the moment the interval ticks over."
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
          label="Cursor field (optional)"
          helperText='The record field to watch for "what changed", e.g. "updated_at" or "id". Leave both this and the field below blank for a plain full re-sync every time.'
        >
          <InputGroup
            value={form.cursorProperty}
            onChange={(e) => setForm((f) => ({ ...f, cursorProperty: e.target.value }))}
            placeholder="updated_at"
          />
        </FormGroup>
        <FormGroup
          label="Incremental query parameter (optional)"
          helperText='Sent as e.g. "?updated_since=<last seen value>" on every sync after the first. Only helps if this API actually understands that parameter — if it ignores it, syncs still work, just without the reduced load.'
        >
          <InputGroup
            value={form.incrementalParam}
            onChange={(e) => setForm((f) => ({ ...f, incrementalParam: e.target.value }))}
            placeholder="updated_since"
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
              disabled={!form.name || !form.baseUrl || (form.authMethod === "connection" && !form.connectionName)}
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
