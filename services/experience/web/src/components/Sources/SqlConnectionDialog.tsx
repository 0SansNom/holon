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
  InputGroup,
} from "@blueprintjs/core";
import { useRegisterSqlConnection, useBootstrapConfig } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SqlConnection, SqlDialect } from "../../api/connectivity";
import { SecretRefField } from "./ConnectionFields";

const DEFAULT_PORTS: Record<SqlDialect, number> = {
  postgres: 5432,
  alloydb: 5432,
  cockroachdb: 26257,
  enterprisedb: 5444,
  greenplum: 5432,
  mysql: 3306,
  mariadb: 3306,
  singlestore: 3306,
  mssql: 1433,
  azure_synapse: 1433,
  azure_synapse_serverless: 1433,
  snowflake: 443,
};

const DIALECT_LABELS: Record<SqlDialect, string> = {
  postgres: "PostgreSQL",
  alloydb: "AlloyDB",
  cockroachdb: "CockroachDB",
  enterprisedb: "EnterpriseDB",
  greenplum: "Greenplum",
  mysql: "MySQL",
  mariadb: "MariaDB",
  singlestore: "SingleStore",
  mssql: "SQL Server",
  azure_synapse: "Azure Synapse",
  azure_synapse_serverless: "Azure Synapse (serverless)",
  snowflake: "Snowflake",
};

const TLS_BY_DEFAULT = new Set<SqlDialect>([
  "alloydb",
  "cockroachdb",
  "azure_synapse",
  "azure_synapse_serverless",
]);

const DIALECT_OPTIONS = (Object.keys(DIALECT_LABELS) as SqlDialect[]).filter(
  (dialect) => dialect !== "azure_synapse_serverless",
);

function isSynapse(dialect: SqlDialect): boolean {
  return dialect === "azure_synapse" || dialect === "azure_synapse_serverless";
}

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
  const [useTls, setUseTls] = useState(editing != null ? editing.use_tls === true : TLS_BY_DEFAULT.has("postgres"));
  const [error, setError] = useState<string | null>(null);
  const register = useRegisterSqlConnection();

  function onDialectChange(next: SqlDialect) {
    setDialect(next);
    if (isDefaultPort(port, dialect)) {
      setPort(String(DEFAULT_PORTS[next]));
    }
    if (!isEditing) {
      setUseTls(TLS_BY_DEFAULT.has(next));
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
        use_tls: dialect === "snowflake" ? undefined : useTls,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the SQL connection");
    }
  }

  const secretOk = isEditing || Boolean(secretRef) || (!requireSecretRef && Boolean(password));
  const isSnowflake = dialect === "snowflake";
  const synapse = isSynapse(dialect);
  const dialectSelectValue: SqlDialect = dialect === "azure_synapse_serverless" ? "azure_synapse" : dialect;

  return (
    <Dialog isOpen title={isEditing ? "Edit SQL connection" : "New SQL connection"} onClose={onClose} style={{ width: 480 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          {isEditing
            ? "Update host, database, or credentials — the name stays fixed since SQL sources already reference it."
            : "PostgreSQL-compatible (AlloyDB, CockroachDB, …), MySQL/MariaDB/SingleStore, SQL Server/Synapse, or Snowflake. Register once, point several SQL sources at it."}
        </p>
        <FormGroup label="Name" helperText="e.g. erp_prod — referenced by SQL sources, not a dataset name">
          <InputGroup value={name} onChange={(e) => setName(e.target.value)} placeholder="my_db" disabled={isEditing} />
        </FormGroup>
        <FormGroup
          label="Dialect"
          helperText={
            dialect === "singlestore"
              ? "MySQL protocol, port 3306. SingleStore has no Postgres listener."
              : undefined
          }
        >
          <HTMLSelect
            fill
            value={dialectSelectValue}
            onChange={(e) => onDialectChange(e.target.value as SqlDialect)}
            disabled={isEditing}
          >
            {DIALECT_OPTIONS.map((d) => (
              <option key={d} value={d}>
                {DIALECT_LABELS[d]}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        {synapse && (
          <FormGroup
            label="Pool"
            helperText={
              dialect === "azure_synapse_serverless"
                ? "Serverless allows OPENROWSET for files. Host looks like workspace-ondemand.sql.azuresynapse.net."
                : "Dedicated pools reject OPENROWSET. Host looks like workspace.sql.azuresynapse.net."
            }
          >
            <HTMLSelect
              fill
              value={dialect === "azure_synapse_serverless" ? "serverless" : "dedicated"}
              disabled={isEditing}
              onChange={(e) =>
                onDialectChange(
                  e.target.value === "serverless" ? "azure_synapse_serverless" : "azure_synapse",
                )
              }
            >
              <option value="dedicated">Dedicated</option>
              <option value="serverless">Serverless</option>
            </HTMLSelect>
          </FormGroup>
        )}
        <FormGroup
          label={isSnowflake ? "Account / host" : "Host"}
          helperText={
            isSnowflake
              ? "Account locator (xy12345.eu-central-1) or full *.snowflakecomputing.com hostname"
              : undefined
          }
        >
          <InputGroup
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder={isSnowflake ? "xy12345.eu-central-1" : "db.example.com"}
          />
        </FormGroup>
        <FormGroup label="Port">
          <InputGroup
            type="number"
            value={port}
            onChange={(e) => setPort(e.target.value)}
            placeholder={String(DEFAULT_PORTS[dialect])}
          />
        </FormGroup>
        <FormGroup label="Database">
          <InputGroup value={database} onChange={(e) => setDatabase(e.target.value)} placeholder="analytics" />
        </FormGroup>
        {isSnowflake && (
          <FormGroup
            label="Warehouse"
            helperText="Optional compute warehouse. Leave blank to use the user default."
          >
            <InputGroup
              value={warehouse}
              onChange={(e) => setWarehouse(e.target.value)}
              placeholder="COMPUTE_WH"
            />
          </FormGroup>
        )}
        {!isSnowflake && (
          <FormGroup
            helperText="Verified TLS with the system trust store. Uncheck for a local proxy. Changing this on an existing connection requires the secret again."
          >
            <Checkbox
              checked={useTls}
              label="Require TLS"
              onChange={(e) => setUseTls((e.target as HTMLInputElement).checked)}
            />
          </FormGroup>
        )}
        <FormGroup label="Username">
          <InputGroup value={username} onChange={(e) => setUsername(e.target.value)} placeholder="readonly_user" />
        </FormGroup>
        {!requireSecretRef && (
          <FormGroup
            label="Password"
            helperText={isEditing && editing?.has_password ? "A password is already set — leave blank to keep it." : undefined}
          >
            <InputGroup
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={isEditing && editing?.has_password ? "•••••••• (unchanged)" : "••••••••"}
            />
          </FormGroup>
        )}
        <SecretRefField
          id="sql-connection-secret-ref"
          value={secretRef}
          onChange={setSecretRef}
          placeholder="env:HOLON_CONN_<TENANT>__ERP_PASSWORD"
        />
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
