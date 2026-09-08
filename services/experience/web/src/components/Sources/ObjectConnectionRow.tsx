import { useState } from "react";
import { Alert, Button, Card, Tag } from "@blueprintjs/core";
import { useDeleteObjectConnection } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { ObjectConnection } from "../../api/connectivity";

export function ObjectConnectionRow({ connection, onEdit }: { connection: ObjectConnection; onEdit: () => void }) {
  const del = useDeleteObjectConnection();
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
            {connection.endpoint}
            {connection.kind === "s3" && ` · ${connection.region}`}
          </div>
          <div className="hl-tag-row hl-mt-xs">
            <Tag minimal icon="cloud">
              {connection.kind === "azure" ? "Azure Blob" : "S3"}
            </Tag>
            {connection.kind === "s3" && connection.path_style && (
              <Tag minimal>path-style</Tag>
            )}
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
          Delete <strong>{connection.name}</strong>? Any object source still pointed at it will refuse — repoint or
          delete those first.
        </p>
      </Alert>
    </Card>
  );
}
