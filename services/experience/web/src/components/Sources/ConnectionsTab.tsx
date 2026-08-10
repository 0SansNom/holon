import { useState } from "react";
import { useConnections } from "../../api/hooks";
import type { GenericConnection } from "../../api/connectivity";
import { EmptyState } from "../common/ListPrimitives";
import { OntologyTabHeader } from "../Ontology/OntologyTabLayout";
import { ConnectionRow } from "./ConnectionRow";
import { ConnectionDialog } from "./ConnectionDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";

export function ConnectionsTab() {
  const { data: connections } = useConnections();
  const [creating, setCreating] = useState(false);
  const [editingConnection, setEditingConnection] = useState<GenericConnection | null>(null);
  const dialogOpen = creating || editingConnection !== null;

  usePaletteCreateIntent("create-connection", setCreating);

  function closeDialog() {
    setCreating(false);
    setEditingConnection(null);
  }

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            Reusable credentials — save one, point several sources at it instead of re-entering the same secret. Set
            these up first if a source will use one.
          </>
        }
        createLabel="New connection"
        onCreate={() => setCreating(true)}
      />

      <div className="hl-source-list">
        {connections?.map((c) => (
          <ConnectionRow key={c.name} connection={c} onEdit={() => setEditingConnection(c)} />
        ))}
        {connections?.length === 0 && <EmptyState actionLabel="New connection" onAction={() => setCreating(true)}>No connections saved yet.</EmptyState>}
      </div>
      {dialogOpen && <ConnectionDialog editing={editingConnection} onClose={closeDialog} />}
    </div>
  );
}
