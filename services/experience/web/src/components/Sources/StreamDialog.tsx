import { useState } from "react";
import { Button, Callout, Dialog, DialogBody, DialogFooter, FormGroup, InputGroup } from "@blueprintjs/core";
import { useRegisterKafkaStream } from "../../api/hooks";
import { ApiError } from "../../api/client";

export function StreamDialog({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [keyField, setKeyField] = useState("");
  const [datasetName, setDatasetName] = useState("");
  const [batchIntervalSeconds, setBatchIntervalSeconds] = useState("5");
  const [error, setError] = useState<string | null>(null);
  const register = useRegisterKafkaStream();

  async function save() {
    setError(null);
    try {
      await register.mutateAsync({
        name,
        topic,
        key_field: keyField,
        dataset_name: datasetName,
        batch_interval_seconds: batchIntervalSeconds ? Number(batchIntervalSeconds) : undefined,
      });
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't register the stream");
    }
  }

  return (
    <Dialog isOpen title="New Kafka stream" onClose={onClose} style={{ width: 480 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          Consumes a Kafka topic continuously — no code, no deploy. Each message must be a flat JSON object; the key
          field's value identifies the record, so a later message with the same key replaces the earlier one rather
          than adding a duplicate row.
        </p>
        <FormGroup label="Name" helperText="A label for this stream, not the topic or dataset name">
          <InputGroup value={name} onChange={(e) => setName(e.target.value)} placeholder="inventory-levels-stream" />
        </FormGroup>
        <FormGroup label="Topic">
          <InputGroup value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="external-inventory-stream" />
        </FormGroup>
        <FormGroup label="Key field" helperText="The JSON field naming each record's unique key">
          <InputGroup value={keyField} onChange={(e) => setKeyField(e.target.value)} placeholder="sku" />
        </FormGroup>
        <FormGroup label="Dataset name" helperText="Where synced data lands — same dataset space as sources/plugins">
          <InputGroup value={datasetName} onChange={(e) => setDatasetName(e.target.value)} placeholder="inventory_levels" />
        </FormGroup>
        <FormGroup label="Commit every ___ seconds" helperText="How often a micro-batch is written as a new Iceberg snapshot">
          <InputGroup
            type="number"
            min={1}
            value={batchIntervalSeconds}
            onChange={(e) => setBatchIntervalSeconds(e.target.value)}
            placeholder="5"
          />
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
              disabled={!name || !topic || !keyField || !datasetName}
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
