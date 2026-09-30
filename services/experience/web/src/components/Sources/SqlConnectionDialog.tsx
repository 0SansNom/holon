import { useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter } from "@blueprintjs/core";
import { useRegisterSqlConnection, useBootstrapConfig } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SqlConnection, SqlDialect } from "../../api/connectivity";
import { ConnectionFields, type ConnectionField } from "./ConnectionFields";

const DEFAULT_PORTS: Record<SqlDialect, number> = {
  postgres: 5432,
  mysql: 3306,
  mssql: 1433,
  snowflake: 443,
};

const DIALECT_LABELS: Record<SqlDialect, string> = {
  postgres: "PostgreSQL",
  mysql: "MySQL / MariaDB",
  mssql: "SQL Server",
  snowflake: "Snowflake",
};

const DIALECT_OPTIONS = (Object.keys(DIALECT_LABELS) as SqlDialect[]).map((dialect) => ({
  value: dialect,
  label: DIALECT_LABELS[dialect],
}));

function isDefaultPort(port: string, dialect: SqlDialect): boolean {
  return !port || port === String(DEFAULT_PORTS[dialect]);
}

export function SqlConnectionDialog({ editing, onClose }: { editing: SqlConnection | null; onClose: () => void }) {
  const isEditing = editing !== null;
  const { data: bootstrap } = useBootstrapConfig();
  const requireSecretRef = bootstrap?.require_connector_secret_ref === true;
  const [name, setName] = useState(editing?.name ?? "");
  const [dialect, setDialect] = useState<SqlDialect>(editing?.dialect ?? "postgres");
  const [host, setHost] = useState(editing?.host ?? "");
  const [port, setPort] = useState(editing != null ? String(editing.port) : String(DEFAULT_PORTS.postgres));
  const [database, setDatabase] = useState(editing?.database ?? "");
  const [warehouse, setWarehouse] = useState(editing?.warehouse ?? "");
  const [username, setUsername] = useState(editing?.username ?? "");
  const [password, setPassword] = useState("");
  const [secretRef, setSecretRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const register = useRegisterSqlConnection();

  function onDialectChange(next: string) {
    const dialectNext = next as SqlDialect;
    setDialect(dialectNext);
    if (isDefaultPort(port, dialect)) {
      setPort(String(DEFAULT_PORTS[dialectNext]));
    }
  }

  async function save() {
    setError(null);
    try {
      await register.mutateAsync({
        name,
        dialect,
        host,
        port: Number(port) || DEFAULT_PORTS[dialect],
        database,
        warehouse: dialect === "snowflake" ? warehouse.trim() || undefined : undefined,
        username,
        password: requireSecretRef ? undefined : password || undefined,
        secret_ref: secretRef || undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the SQL connection");
    }
  }

  const secretOk = isEditing || Boolean(secretRef) || (!requireSecretRef && Boolean(password));
  const isSnowflake = dialect === "snowflake";
  const fields: ConnectionField[] = [
    {
      kind: "text",
      id: "sql-connection-name",
      label: "Name",
      helperText: "e.g. erp_prod — referenced by SQL sources, not a dataset name",
      value: name,
      onChange: setName,
      placeholder: "my_db",
      disabled: isEditing,
    },
    {
      kind: "select",
      id: "sql-connection-dialect",
      label: "Dialect",
      value: dialect,
      onChange: onDialectChange,
      options: DIALECT_OPTIONS,
      disabled: isEditing,
    },
    {
      kind: "text",
      id: "sql-connection-host",
      label: isSnowflake ? "Account / host" : "Host",
      helperText: isSnowflake
        ? "Account locator (xy12345.eu-central-1) or full *.snowflakecomputing.com hostname"
        : undefined,
      value: host,
      onChange: setHost,
      placeholder: isSnowflake ? "xy12345.eu-central-1" : "db.example.com",
    },
    {
      kind: "number",
      id: "sql-connection-port",
      label: "Port",
      value: port,
      onChange: setPort,
      placeholder: String(DEFAULT_PORTS[dialect]),
    },
    {
      kind: "text",
      id: "sql-connection-database",
      label: "Database",
      value: database,
      onChange: setDatabase,
      placeholder: "analytics",
    },
    ...(isSnowflake
      ? [
          {
            kind: "text" as const,
            id: "sql-connection-warehouse",
            label: "Warehouse",
            helperText: "Optional compute warehouse. Leave blank to use the user default.",
            value: warehouse ?? "",
            onChange: setWarehouse,
            placeholder: "COMPUTE_WH",
          },
        ]
      : []),
    {
      kind: "text",
      id: "sql-connection-username",
      label: "Username",
      value: username,
      onChange: setUsername,
      placeholder: "readonly_user",
    },
    ...(!requireSecretRef
      ? [
          {
            kind: "secret" as const,
            id: "sql-connection-password",
            label: "Password",
            helperText: isEditing && editing?.has_password ? "A password is already set — leave blank to keep it." : undefined,
            value: password,
            onChange: setPassword,
            placeholder: isEditing && editing?.has_password ? "•••••••• (unchanged)" : "••••••••",
          },
        ]
      : []),
    {
      kind: "secretRef",
      id: "sql-connection-secret-ref",
      value: secretRef,
      onChange: setSecretRef,
      placeholder: "env:HOLON_CONN_<TENANT>__ERP_PASSWORD",
    },
  ];

  return (
    <Dialog isOpen title={isEditing ? "Edit SQL connection" : "New SQL connection"} onClose={onClose} style={{ width: 480 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Update host, database, or credentials — the name stays fixed since SQL sources already reference it."
            : "PostgreSQL, MySQL/MariaDB, SQL Server, or Snowflake. Register once, point several SQL sources at it."}
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
              disabled={!name || !host || !database || !username || !secretOk}
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
