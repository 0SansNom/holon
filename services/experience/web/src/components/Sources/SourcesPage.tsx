import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  Alert,
  Button,
  Callout,
  Card,
  Dialog,
  DialogBody,
  DialogFooter,
  FormGroup,
  H3,
  HTMLSelect,
  Icon,
  InputGroup,
  Spinner,
  Switch,
  Tab,
  Tabs,
  Tag,
} from "@blueprintjs/core";
import {
  useSources,
  useRegisterSource,
  useSyncDataset,
  useSyncs,
  useDisableSource,
  useEnableSource,
  useDeleteSource,
  useDatasetPreview,
  useCreateObjectType,
  useConnections,
  useRegisterConnection,
  useDeleteConnection,
} from "../../api/hooks";
import { ApiError } from "../../api/client";
import { TENANT_ID, WORKSPACE_ID } from "../../api/config";
import type { GenericSource, GenericConnection } from "../../api/connectivity";

type AuthMethod = "none" | "connection" | "inline";

const EMPTY_FORM = {
  name: "",
  baseUrl: "",
  authMethod: "none" as AuthMethod,
  connectionName: "",
  authHeaderName: "",
  authHeaderValue: "",
  recordPath: "",
  nextPagePath: "",
  scheduleIntervalMinutes: "",
  cursorProperty: "",
  incrementalParam: "",
};

function snakeToCamel(s: string): string {
  return s.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());
}

function toPascalCase(s: string): string {
  const camel = snakeToCamel(s);
  return camel.charAt(0).toUpperCase() + camel.slice(1);
}

// Client-side estimate only — the scheduler (`main.py`'s
// `run_scheduler_forever`) is the real source of truth, checking
// roughly once a minute, so this is "about" due, not a countdown to
// trust to the second.
function nextSyncDescription(lastFinishedAt: string, intervalMinutes: number): string {
  const dueAt = new Date(lastFinishedAt).getTime() + intervalMinutes * 60_000;
  const minutesLeft = Math.round((dueAt - Date.now()) / 60_000);
  if (minutesLeft <= 0) return "due now";
  if (minutesLeft < 60) return `in ~${minutesLeft}min`;
  return `in ~${Math.round(minutesLeft / 60)}h`;
}

const CLASSIFICATIONS = ["public", "internal", "confidential", "restricted"] as const;

// The other half of the no-code connector: a Dataset a source has
// already synced becomes a real, browsable ObjectType by naming its
// properties — no code, same "fill a form" shape as connecting the
// source in the first place. `useDatasetPreview` suggests a name per
// column so a non-technical admin never has to guess or type a raw
// JSON key from memory; every suggestion stays fully editable.
function CreateObjectTypeDialog({ source, onClose }: { source: GenericSource; onClose: () => void }) {
  const { data: preview, isLoading: previewLoading, error: previewError } = useDatasetPreview(source.name);
  const [name, setName] = useState(toPascalCase(source.name));
  const [description, setDescription] = useState("");
  const [propertyNames, setPropertyNames] = useState<Record<string, string>>({});
  const [classifications, setClassifications] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<string | null>(null);

  const createType = useCreateObjectType();
  const sync = useSyncDataset();
  const busy = createType.isPending || sync.isPending;

  useEffect(() => {
    if (!preview) return;
    setPropertyNames((current) => {
      if (Object.keys(current).length > 0) return current;
      const suggested: Record<string, string> = {};
      preview.columns.forEach((c) => {
        suggested[c.name] = snakeToCamel(c.name);
      });
      return suggested;
    });
    setClassifications((current) => {
      if (Object.keys(current).length > 0) return current;
      const defaults: Record<string, string> = {};
      preview.columns.forEach((c) => {
        defaults[c.name] = "internal";
      });
      return defaults;
    });
  }, [preview]);

  const propertyMapping = useMemo(() => {
    const mapping: Record<string, string> = {};
    Object.entries(propertyNames).forEach(([column, property]) => {
      if (property.trim()) mapping[property.trim()] = column;
    });
    return mapping;
  }, [propertyNames]);

  async function create() {
    setError(null);
    try {
      const columnClassification: Record<string, string> = {};
      Object.keys(propertyMapping).forEach((property) => {
        const column = propertyMapping[property];
        columnClassification[column] = classifications[column] ?? "internal";
      });
      await createType.mutateAsync({
        name,
        source_dataset_urn: `hl:${TENANT_ID}:${WORKSPACE_ID}:dataset:${source.name}`,
        property_mapping: propertyMapping,
        description,
        column_classification: columnClassification,
      });
      // The sync that landed this data predates the ObjectType, so
      // Knowledge's own consumer skipped materializing/indexing it at
      // the time (logged, not an error) — this second sync is what
      // actually catches it up, the same one-click "Sync now" already
      // on every source row, just triggered automatically here so the
      // admin never needs to know this detail exists.
      await sync.mutateAsync(source.name);
      setCreated(name);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create the Object Type");
    }
  }

  if (created) {
    return (
      <Dialog isOpen title="Create Object Type" onClose={onClose} style={{ width: 480 }}>
        <DialogBody>
          <div style={{ textAlign: "center", padding: "16px 0" }}>
            <Icon icon="tick-circle" size={36} intent="success" />
            <p style={{ fontSize: 15, fontWeight: 600, margin: "12px 0 4px" }}>Created</p>
            <p style={{ fontSize: 13, color: "var(--hl-text-muted)", margin: 0 }}>
              <span className="hl-mono">{created}</span> is browsable now, under Objects.
            </p>
          </div>
        </DialogBody>
        <DialogFooter
          actions={
            <>
              <Button onClick={onClose}>Close</Button>
              <Link to="/objects/$type" params={{ type: created }} className="bp6-button bp6-intent-primary" onClick={onClose}>
                View {created}
              </Link>
            </>
          }
        />
      </Dialog>
    );
  }

  return (
    <Dialog isOpen title="Create Object Type" onClose={onClose} style={{ width: 580 }}>
      <DialogBody>
        <p style={{ fontSize: 12.5, color: "var(--hl-text-muted)", marginTop: 0 }}>
          Turn <span className="hl-mono">{source.name}</span>'s synced data into a real, browsable Object Type —
          name its properties below, suggested from the columns actually in the data.
        </p>
        <FormGroup label="Object Type name">
          <InputGroup value={name} onChange={(e) => setName(e.target.value)} placeholder="MyObjectType" />
        </FormGroup>
        <FormGroup label="Description (optional)">
          <InputGroup value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What is this?" />
        </FormGroup>

        {previewLoading && <Spinner size={24} />}
        {previewError && (
          <Callout intent="warning" icon="warning-sign">
            Couldn't read this source's columns — sync it at least once first (use "Sync now" on its row), then try
            again.
          </Callout>
        )}
        {preview && preview.columns.length > 0 && (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "0.8fr 1fr 0.9fr", gap: "4px 10px", margin: "12px 0 6px" }}>
              <span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.03em", color: "var(--hl-text-muted)" }}>
                Column
              </span>
              <span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.03em", color: "var(--hl-text-muted)" }}>
                Property
              </span>
              <span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.03em", color: "var(--hl-text-muted)" }}>
                Sensitivity
              </span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "0.8fr 1fr 0.9fr", gap: "6px 10px", alignItems: "center" }}>
              {preview.columns.map((c) => (
                <Fragment key={c.name}>
                  <span className="hl-mono" style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
                    {c.name}
                  </span>
                  <InputGroup
                    small
                    value={propertyNames[c.name] ?? ""}
                    onChange={(e) => setPropertyNames((p) => ({ ...p, [c.name]: e.target.value }))}
                    placeholder="(skip this column)"
                  />
                  <HTMLSelect
                    fill
                    minimal
                    disabled={!propertyNames[c.name]?.trim()}
                    value={classifications[c.name] ?? "internal"}
                    onChange={(e) => setClassifications((cls) => ({ ...cls, [c.name]: e.target.value }))}
                  >
                    {CLASSIFICATIONS.map((level) => (
                      <option key={level} value={level}>
                        {level}
                      </option>
                    ))}
                  </HTMLSelect>
                </Fragment>
              ))}
            </div>
            <p style={{ fontSize: 11, color: "var(--hl-text-muted)", marginTop: 6 }}>
              "Confidential"/"restricted" columns are actually masked from principals without clearance — not just a
              label, enforced on every read.
            </p>
          </>
        )}

        {error && (
          <Callout intent="danger" style={{ marginTop: 12 }} title="Couldn't create">
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
              disabled={!name || !preview || preview.columns.length === 0 || Object.keys(propertyMapping).length === 0}
              onClick={() => void create()}
            >
              Create
            </Button>
          </>
        }
      />
    </Dialog>
  );
}

function formFromSource(source: GenericSource) {
  const authMethod: AuthMethod = source.connection_name ? "connection" : source.auth_header_name ? "inline" : "none";
  return {
    name: source.name,
    baseUrl: source.base_url,
    authMethod,
    connectionName: source.connection_name ?? "",
    authHeaderName: source.auth_header_name ?? "",
    authHeaderValue: "",
    recordPath: source.record_path ?? "",
    nextPagePath: source.next_page_path ?? "",
    scheduleIntervalMinutes: source.schedule_interval_minutes != null ? String(source.schedule_interval_minutes) : "",
    cursorProperty: source.cursor_property ?? "",
    incrementalParam: source.incremental_param ?? "",
  };
}

// Two distinct screens, never both on screen at once: the form (create
// or edit) and a success confirmation with exactly two unambiguous next
// actions. Also doubles as the edit flow — `editing` pre-fills
// everything except the secret, which the API never echoes back
// (`has_auth_header_value` only says whether one exists), so the value
// field starts blank with a "leave blank to keep it" hint rather than
// lying about there being nothing configured.
function ConnectSourceDialog({ editing, onClose }: { editing: GenericSource | null; onClose: () => void }) {
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
          <div style={{ textAlign: "center", padding: "16px 0" }}>
            <Icon icon="tick-circle" size={36} intent="success" />
            <p style={{ fontSize: 15, fontWeight: 600, margin: "12px 0 4px" }}>{isEditing ? "Saved" : "Connected"}</p>
            <p style={{ fontSize: 13, color: "var(--hl-text-muted)", margin: 0 }}>
              Synced {connected.rowCount} record{connected.rowCount === 1 ? "" : "s"} from{" "}
              <span className="hl-mono">{connected.name}</span>.
            </p>
          </div>
          <Callout intent="primary" icon="info-sign" style={{ marginTop: 8 }}>
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
        <p style={{ fontSize: 12.5, color: "var(--hl-text-muted)", marginTop: 0 }}>
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
          <Callout intent="danger" style={{ marginTop: 8 }} title="Couldn't connect">
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

function SourceRow({ source, lastSync, onEdit }: { source: GenericSource; lastSync: { rowCount: number; finishedAt: string } | undefined; onEdit: () => void }) {
  const sync = useSyncDataset();
  const disable = useDisableSource();
  const enable = useEnableSource();
  const del = useDeleteSource();
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [creatingType, setCreatingType] = useState(false);

  const busy = sync.isPending || disable.isPending || enable.isPending || del.isPending;

  async function syncNow() {
    setResult(null);
    try {
      const res = await sync.mutateAsync(source.name);
      setResult({ ok: true, message: `${res.row_count} record${res.row_count === 1 ? "" : "s"} synced` });
    } catch (err) {
      setResult({ ok: false, message: err instanceof ApiError ? err.message : "Sync failed" });
    }
  }

  async function toggleActive() {
    setResult(null);
    try {
      if (source.status === "active") await disable.mutateAsync(source.name);
      else await enable.mutateAsync(source.name);
    } catch (err) {
      setResult({ ok: false, message: err instanceof ApiError ? err.message : "Couldn't update status" });
    }
  }

  return (
    <Card style={{ marginBottom: 8, opacity: source.status === "disabled" ? 0.65 : 1 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <strong>{source.name}</strong>
          <div className="hl-mono" style={{ fontSize: 11, color: "var(--hl-text-muted)", marginTop: 4 }}>
            {source.base_url}
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            {source.connection_name && <Tag minimal icon="link">{source.connection_name}</Tag>}
            {!source.connection_name && source.auth_header_name && <Tag minimal icon="key">{source.auth_header_name}</Tag>}
            {source.record_path && <Tag minimal icon="key-tab">{source.record_path}</Tag>}
            {source.next_page_path && <Tag minimal icon="numbered-list">paginated</Tag>}
            {source.schedule_interval_minutes != null && (
              <Tag minimal icon="time">
                every {source.schedule_interval_minutes}min
              </Tag>
            )}
            {source.cursor_property && source.incremental_param && (
              <Tag minimal icon="fast-forward" title={`Watches ${source.cursor_property}, sent as ${source.incremental_param}`}>
                incremental
              </Tag>
            )}
          </div>
          <p style={{ fontSize: 11.5, color: "var(--hl-text-muted)", margin: "8px 0 0" }}>
            {lastSync
              ? `Last synced ${new Date(lastSync.finishedAt).toLocaleString()} — ${lastSync.rowCount} record${lastSync.rowCount === 1 ? "" : "s"}`
              : "Never synced"}
            {source.schedule_interval_minutes == null
              ? " — manual only"
              : lastSync
                ? ` — next sync ~${nextSyncDescription(lastSync.finishedAt, source.schedule_interval_minutes)}`
                : " — will sync automatically shortly"}
          </p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
          <Switch
            checked={source.status === "active"}
            label={source.status === "active" ? "Active" : "Disabled"}
            disabled={busy}
            onChange={() => void toggleActive()}
            style={{ marginBottom: 0 }}
          />
          <div style={{ display: "flex", gap: 6 }}>
            <Button small icon="cube-add" onClick={() => setCreatingType(true)} disabled={busy}>
              Create Object Type
            </Button>
            <Button small icon="edit" onClick={onEdit} disabled={busy}>
              Edit
            </Button>
            <Button small icon="refresh" loading={sync.isPending} disabled={source.status !== "active" || busy} onClick={() => void syncNow()}>
              Sync now
            </Button>
            <Button small icon="trash" intent="danger" minimal disabled={busy} onClick={() => setConfirmingDelete(true)} />
          </div>
        </div>
      </div>
      {result && (
        <p style={{ fontSize: 12, marginTop: 8, marginBottom: 0, color: result.ok ? "var(--hl-success)" : "var(--hl-danger)" }}>
          {result.message}
        </p>
      )}
      <Alert
        isOpen={confirmingDelete}
        intent="danger"
        icon="trash"
        confirmButtonText="Delete"
        cancelButtonText="Cancel"
        onConfirm={() => void del.mutateAsync(source.name)}
        onCancel={() => setConfirmingDelete(false)}
      >
        <p>
          Delete <strong>{source.name}</strong>? Its configuration is removed permanently — already-synced data in
          Iceberg is not affected, but you'd need to reconnect from scratch to sync it again.
        </p>
      </Alert>
      {creatingType && <CreateObjectTypeDialog source={source} onClose={() => setCreatingType(false)} />}
    </Card>
  );
}

// Doubles as the edit flow, same "same dialog, `editing` pre-fills
// everything except the secret" pattern `ConnectSourceDialog` already
// uses — the name is locked once editing (it's the upsert key), and the
// auth header value starts blank with a "leave blank to keep it" hint
// rather than lying about there being nothing configured, since the API
// never echoes a stored secret back.
function ConnectionDialog({ editing, onClose }: { editing: GenericConnection | null; onClose: () => void }) {
  const isEditing = editing !== null;
  const [name, setName] = useState(editing?.name ?? "");
  const [authHeaderName, setAuthHeaderName] = useState(editing?.auth_header_name ?? "");
  const [authHeaderValue, setAuthHeaderValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const register = useRegisterConnection();

  async function save() {
    setError(null);
    try {
      await register.mutateAsync({
        name,
        auth_header_name: authHeaderName,
        auth_header_value: authHeaderValue || undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the connection");
    }
  }

  return (
    <Dialog isOpen title={isEditing ? "Edit connection" : "New connection"} onClose={onClose} style={{ width: 440 }}>
      <DialogBody>
        <p style={{ fontSize: 12.5, color: "var(--hl-text-muted)", marginTop: 0 }}>
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
        {error && (
          <Callout intent="danger" style={{ marginTop: 8 }} title="Couldn't save">
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
              disabled={!name || !authHeaderName || (!isEditing && !authHeaderValue)}
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

function ConnectionRow({ connection, onEdit }: { connection: GenericConnection; onEdit: () => void }) {
  const del = useDeleteConnection();
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  async function confirmDelete() {
    setConfirmingDelete(false);
    setError(null);
    try {
      await del.mutateAsync(connection.name);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete");
    }
  }

  return (
    <Card style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <strong>{connection.name}</strong>
          <div style={{ marginTop: 4 }}>
            <Tag minimal icon="key">
              {connection.auth_header_name}
            </Tag>
          </div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <Button small icon="edit" onClick={onEdit} disabled={del.isPending}>
            Edit
          </Button>
          <Button small icon="trash" intent="danger" minimal loading={del.isPending} onClick={() => setConfirmingDelete(true)} />
        </div>
      </div>
      {error && (
        <p style={{ fontSize: 12, marginTop: 8, marginBottom: 0, color: "var(--hl-danger)" }}>{error}</p>
      )}
      <Alert
        isOpen={confirmingDelete}
        intent="danger"
        icon="trash"
        confirmButtonText="Delete"
        cancelButtonText="Cancel"
        onConfirm={() => void confirmDelete()}
        onCancel={() => setConfirmingDelete(false)}
      >
        <p>
          Delete <strong>{connection.name}</strong>? Any source still pointed at it will refuse — repoint or delete
          those first.
        </p>
      </Alert>
    </Card>
  );
}

function ConnectionsTab() {
  const { data: connections, isLoading } = useConnections();
  const [creating, setCreating] = useState(false);
  const [editingConnection, setEditingConnection] = useState<GenericConnection | null>(null);
  const dialogOpen = creating || editingConnection !== null;

  function closeDialog() {
    setCreating(false);
    setEditingConnection(null);
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <p style={{ color: "var(--hl-text-muted)", margin: 0, maxWidth: 640, fontSize: 12.5 }}>
          Reusable credentials — save one, point several sources at it instead of re-entering the same secret. Set
          these up first if a source will use one.
        </p>
        <Button small icon="add" onClick={() => setCreating(true)} style={{ flexShrink: 0, marginLeft: 12 }}>
          New connection
        </Button>
      </div>
      <div style={{ marginTop: 16 }}>
        {isLoading && <Spinner size={20} />}
        {connections?.map((c) => (
          <ConnectionRow key={c.name} connection={c} onEdit={() => setEditingConnection(c)} />
        ))}
        {connections?.length === 0 && <p style={{ color: "var(--hl-text-muted)", fontSize: 13 }}>No connections saved yet.</p>}
      </div>
      {dialogOpen && <ConnectionDialog editing={editingConnection} onClose={closeDialog} />}
    </div>
  );
}

function DataSourcesTab() {
  const { data: sources, isLoading } = useSources();
  const { data: syncs } = useSyncs();
  const [connecting, setConnecting] = useState(false);
  const [editingSource, setEditingSource] = useState<GenericSource | null>(null);

  // Most recent sync_run per dataset — `/syncs` is already ordered
  // newest-first, so the first match per dataset_urn is the latest.
  const lastSyncByName = useMemo(() => {
    const map = new Map<string, { rowCount: number; finishedAt: string }>();
    (syncs ?? []).forEach((run) => {
      const name = run.dataset_urn.split(":").pop();
      if (name && !map.has(name)) map.set(name, { rowCount: run.row_count, finishedAt: run.finished_at });
    });
    return map;
  }, [syncs]);

  const dialogOpen = connecting || editingSource !== null;

  function closeDialog() {
    setConnecting(false);
    setEditingSource(null);
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <p style={{ color: "var(--hl-text-muted)", margin: 0, maxWidth: 640 }}>
          Connect any JSON REST API by URL — no code, no deploy. Once synced, a source's data is catalogued the same
          way as every built-in connector; mapping it to an Object Type is the next step, under Admin.
        </p>
        <Button intent="primary" icon="add" onClick={() => setConnecting(true)} style={{ flexShrink: 0, marginLeft: 12 }}>
          Connect a source
        </Button>
      </div>

      <div style={{ marginTop: 16 }}>
        {isLoading && <Spinner />}
        {sources?.map((s) => (
          <SourceRow key={s.name} source={s} lastSync={lastSyncByName.get(s.name)} onEdit={() => setEditingSource(s)} />
        ))}
        {sources?.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No sources connected yet.</p>}
      </div>

      {dialogOpen && <ConnectSourceDialog editing={editingSource} onClose={closeDialog} />}
    </div>
  );
}

export function SourcesPage() {
  return (
    <div>
      <H3>Data Sources</H3>
      <Tabs id="sources-tabs" renderActiveTabPanelOnly>
        <Tab id="data-sources" title="Data Sources" panel={<DataSourcesTab />} />
        <Tab id="connections" title="Connections" panel={<ConnectionsTab />} />
      </Tabs>
    </div>
  );
}
