import { useState } from "react";
import { Alert, Button, Card, Tag } from "@blueprintjs/core";
import { useDeleteConnection } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { GenericConnection } from "../../api/connectivity";

export function ConnectionRow({ connection, onEdit }: { connection: GenericConnection; onEdit: () => void }) {
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
    <Card className="hl-source-row">
      <div className="hl-flex-between hl-items-center">
        <div>
          <strong>{connection.name}</strong>
          <div className="hl-mt-xs">
            <Tag minimal icon="key">
              {connection.auth_header_name}
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
          Delete <strong>{connection.name}</strong>? Any source still pointed at it will refuse — repoint or delete
          those first.
        </p>
      </Alert>
    </Card>
  );
}
