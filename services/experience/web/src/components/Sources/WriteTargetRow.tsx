import { useState } from "react";
import { Alert, Button, Card, Tag } from "@blueprintjs/core";
import { useDeleteWriteTarget } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { WriteTarget } from "../../api/connectivity";

export function WriteTargetRow({ target }: { target: WriteTarget }) {
  const del = useDeleteWriteTarget();
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  async function confirmDelete() {
    setConfirmingDelete(false);
    setError(null);
    try {
      await del.mutateAsync(target.dataset_name);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete");
    }
  }

  return (
    <Card className="hl-source-row">
      <div className="hl-flex-between hl-items-center">
        <div>
          <strong>{target.dataset_name}</strong>
          <div className="hl-mono hl-text-muted-sm hl-mt-xs">
            {target.table_name} (id: {target.id_column})
          </div>
          <div className="hl-tag-row hl-mt-xs">
            {Object.entries(target.allowed_properties).map(([property, column]) => (
              <Tag key={property} minimal icon="key-tab">
                {property} → {column}
              </Tag>
            ))}
          </div>
        </div>
        <div className="hl-source-row-buttons">
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
          Delete the write target for <strong>{target.dataset_name}</strong>? Any declarative Action with this as
          its <span className="hl-mono">writeback_dataset</span> will fail its writeback step until a new target is
          registered.
        </p>
      </Alert>
    </Card>
  );
}
