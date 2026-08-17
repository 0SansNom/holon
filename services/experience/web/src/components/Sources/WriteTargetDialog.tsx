import { useMemo, useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter, FormGroup, InputGroup } from "@blueprintjs/core";
import { useRegisterWriteTarget } from "../../api/hooks";
import { ApiError } from "../../api/client";

interface PropertyRow {
  property: string;
  column: string;
}

export function WriteTargetDialog({ onClose }: { onClose: () => void }) {
  const [datasetName, setDatasetName] = useState("");
  const [tableName, setTableName] = useState("");
  const [idColumn, setIdColumn] = useState("id");
  const [rows, setRows] = useState<PropertyRow[]>([{ property: "", column: "" }]);
  const [error, setError] = useState<string | null>(null);
  const register = useRegisterWriteTarget();

  const allowedProperties = useMemo(() => {
    const mapping: Record<string, string> = {};
    rows.forEach(({ property, column }) => {
      if (property.trim() && column.trim()) mapping[property.trim()] = column.trim();
    });
    return mapping;
  }, [rows]);

  function updateRow(index: number, field: keyof PropertyRow, value: string) {
    setRows((current) => current.map((r, i) => (i === index ? { ...r, [field]: value } : r)));
  }

  function addRow() {
    setRows((current) => [...current, { property: "", column: "" }]);
  }

  function removeRow(index: number) {
    setRows((current) => current.filter((_, i) => i !== index));
  }

  async function save() {
    setError(null);
    try {
      await register.mutateAsync({
        dataset_name: datasetName,
        table_name: tableName,
        id_column: idColumn,
        allowed_properties: allowedProperties,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't register the write target");
    }
  }

  return (
    <Dialog isOpen title="New write target" onClose={onClose} style={{ width: 520 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          Names a real source table and an explicit allow-list of properties a governed Action's writeback may
          touch — the actual write still only ever happens through an Action with a high-risk approval, never
          directly.
        </p>
        <FormGroup label="Dataset name" helperText="The Action Type's writeback_dataset — must match exactly">
          <InputGroup value={datasetName} onChange={(e) => setDatasetName(e.target.value)} placeholder="customers" />
        </FormGroup>
        <FormGroup label="Table name" helperText="The real table in the source database">
          <InputGroup value={tableName} onChange={(e) => setTableName(e.target.value)} placeholder="customers" />
        </FormGroup>
        <FormGroup label="Id column">
          <InputGroup value={idColumn} onChange={(e) => setIdColumn(e.target.value)} placeholder="id" />
        </FormGroup>
        <FormGroup label="Allowed properties" helperText="Ontology property name → source column name">
          {rows.map((row, i) => (
            <div key={i} className="hl-flex-row hl-mb-xs" style={{ gap: 8 }}>
              <InputGroup
                small
                value={row.property}
                onChange={(e) => updateRow(i, "property", e.target.value)}
                placeholder="accountClosed"
                style={{ flex: 1 }}
              />
              <InputGroup
                small
                value={row.column}
                onChange={(e) => updateRow(i, "column", e.target.value)}
                placeholder="account_closed"
                style={{ flex: 1 }}
              />
              <Button small icon="cross" minimal disabled={rows.length === 1} onClick={() => removeRow(i)} />
            </div>
          ))}
          <Button small icon="add" minimal onClick={addRow}>
            Add property
          </Button>
        </FormGroup>
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
              disabled={!datasetName || !tableName || !idColumn || Object.keys(allowedProperties).length === 0}
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
