import { useState } from "react";
import { Alert, Button, Card, Tag } from "@blueprintjs/core";
import { useDeleteSqlConnection } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SqlConnection } from "../../api/connectivity";

export function SqlConnectionRow({ connection, onEdit }: { connection: SqlConnection; onEdit: () => void }) {
  const del = useDeleteSqlConnection();
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
    <Card className="hl-source-row">
      <div className="hl-flex-between hl-items-center">
        <div>
          <strong>{connection.name}</strong>
          <div className="hl-mono hl-text-muted-sm hl-mt-xs">
            {connection.username}@{connection.host}:{connection.port}/{connection.database}
          </div>
          <div className="hl-tag-row hl-mt-xs">
            <Tag minimal icon="database">
              SQL
            </Tag>
          </div>
        </div>
        <div className="hl-source-row-buttons">
          <Button small icon="edit" onClick={onEdit} disabled={del.isPending}>
            Edit
          </Button>
          <Button small icon="trash" intent="danger" minimal loading={del.isPending} onClick={() => setConfirmingDelete(true)} />
        </div>
      </div>
      {error && <p className="hl-text-danger hl-text-muted-sm hl-mt-sm">{error}</p>}
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
          Delete <strong>{connection.name}</strong>? Any SQL source still pointed at it will refuse — repoint or delete
          those first.
        </p>
      </Alert>
    </Card>
  );
}
