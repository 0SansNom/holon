import { useState } from "react";
import { Button, Menu, MenuItem, PopoverNext } from "@blueprintjs/core";
import { useConnections, useSqlConnections, useObjectConnections } from "../../api/hooks";
import type { GenericConnection, SqlConnection, ObjectConnection } from "../../api/connectivity";
import { EmptyState } from "../common/ListPrimitives";
import { OntologyTabHeader } from "../Ontology/OntologyTabLayout";
import { ConnectionRow } from "./ConnectionRow";
import { ConnectionDialog } from "./ConnectionDialog";
import { SqlConnectionRow } from "./SqlConnectionRow";
import { SqlConnectionDialog } from "./SqlConnectionDialog";
import { ObjectConnectionRow } from "./ObjectConnectionRow";
import { ObjectConnectionDialog } from "./ObjectConnectionDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";

export function ConnectionsTab() {
  const { data: connections } = useConnections();
  const { data: sqlConnections } = useSqlConnections();
  const { data: objectConnections } = useObjectConnections();
  const [creatingRest, setCreatingRest] = useState(false);
  const [creatingSql, setCreatingSql] = useState(false);
  const [creatingObject, setCreatingObject] = useState(false);
  const [editingConnection, setEditingConnection] = useState<GenericConnection | null>(null);
  const [editingSqlConnection, setEditingSqlConnection] = useState<SqlConnection | null>(null);
  const [editingObjectConnection, setEditingObjectConnection] = useState<ObjectConnection | null>(null);

  usePaletteCreateIntent("create-connection", setCreatingRest);
  usePaletteCreateIntent("create-sql-connection", setCreatingSql);
  usePaletteCreateIntent("create-object-connection", setCreatingObject);

  const restDialogOpen = creatingRest || editingConnection !== null;
  const sqlDialogOpen = creatingSql || editingSqlConnection !== null;
  const objectDialogOpen = creatingObject || editingObjectConnection !== null;

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            Reusable credentials for REST, SQL, and object storage — register once, point several sources at the same
            connection.
          </>
        }
        trailing={
          <PopoverNext
            placement="bottom-end"
            content={
              <Menu>
                <MenuItem icon="key" text="REST credential" onClick={() => setCreatingRest(true)} />
                <MenuItem icon="database" text="SQL connection" onClick={() => setCreatingSql(true)} />
                <MenuItem icon="cloud" text="Object storage connection" onClick={() => setCreatingObject(true)} />
              </Menu>
            }
          >
            <Button intent="primary" icon="add" rightIcon="caret-down">
              New connection
            </Button>
          </PopoverNext>
        }
      />

      <div className="hl-section-title hl-mb-sm">REST credentials</div>
      <div className="hl-source-list hl-mb-lg">
        {connections?.map((c) => (
          <ConnectionRow key={c.name} connection={c} onEdit={() => setEditingConnection(c)} />
        ))}
        {connections?.length === 0 && (
          <EmptyState actionLabel="New REST connection" onAction={() => setCreatingRest(true)}>
            No REST connections saved yet.
          </EmptyState>
        )}
      </div>

      <div className="hl-flex-between hl-mb-sm">
        <div className="hl-section-title">SQL connections</div>
        <Button small icon="add" minimal onClick={() => setCreatingSql(true)}>
          New SQL connection
        </Button>
      </div>
      <p className="hl-text-muted-sm hl-mb-sm">
        Postgres-wire databases (PostgreSQL, Redshift, CockroachDB). Required before registering a SQL source.
      </p>
      <div className="hl-source-list hl-mb-lg">
        {sqlConnections?.map((c) => (
          <SqlConnectionRow key={c.name} connection={c} onEdit={() => setEditingSqlConnection(c)} />
        ))}
        {sqlConnections?.length === 0 && (
          <EmptyState actionLabel="New SQL connection" onAction={() => setCreatingSql(true)}>
            No SQL connections saved yet.
          </EmptyState>
        )}
      </div>

      <div className="hl-flex-between hl-mb-sm">
        <div className="hl-section-title">Object storage connections</div>
        <Button small icon="add" minimal onClick={() => setCreatingObject(true)}>
          New object connection
        </Button>
      </div>
      <p className="hl-text-muted-sm hl-mb-sm">
        S3-compatible endpoints (MinIO, AWS S3). Required before registering an object source.
      </p>
      <div className="hl-source-list">
        {objectConnections?.map((c) => (
          <ObjectConnectionRow key={c.name} connection={c} onEdit={() => setEditingObjectConnection(c)} />
        ))}
        {objectConnections?.length === 0 && (
          <EmptyState actionLabel="New object connection" onAction={() => setCreatingObject(true)}>
            No object storage connections saved yet.
          </EmptyState>
        )}
      </div>

      {restDialogOpen && (
        <ConnectionDialog
          editing={editingConnection}
          onClose={() => {
            setCreatingRest(false);
            setEditingConnection(null);
          }}
        />
      )}
      {sqlDialogOpen && (
        <SqlConnectionDialog
          editing={editingSqlConnection}
          onClose={() => {
            setCreatingSql(false);
            setEditingSqlConnection(null);
          }}
        />
      )}
      {objectDialogOpen && (
        <ObjectConnectionDialog
          editing={editingObjectConnection}
          onClose={() => {
            setCreatingObject(false);
            setEditingObjectConnection(null);
          }}
        />
      )}
    </div>
  );
}
