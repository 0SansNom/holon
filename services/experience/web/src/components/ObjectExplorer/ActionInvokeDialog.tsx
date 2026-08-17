import { Button, Dialog, DialogBody, DialogFooter, InputGroup, Callout } from "@blueprintjs/core";
import type { ActionDefinition } from "../../api/knowledge";
import { ActionParameterFields } from "../common/ActionParameterFields";

export function ActionInvokeDialog({
  action,
  reason,
  parameters,
  loading,
  currentObjectId,
  currentObject,
  bulkCount = 1,
  bulkCapWarning,
  onReasonChange,
  onParametersChange,
  onClose,
  onSubmit,
}: {
  action: ActionDefinition | undefined;
  reason: string;
  parameters: Record<string, unknown>;
  loading: boolean;
  currentObjectId?: string | null;
  currentObject?: Record<string, unknown> | null;
  /** When > 1, dialog runs as a batch invoke. */
  bulkCount?: number;
  bulkCapWarning?: string | null;
  onReasonChange: (reason: string) => void;
  onParametersChange: (parameters: Record<string, unknown>) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  const isBulk = bulkCount > 1;
  const title = action
    ? isBulk
      ? `${action.name} · ${bulkCount} objects`
      : action.name
    : "";

  return (
    <Dialog isOpen={action !== undefined} onClose={onClose} title={title}>
      <DialogBody>
        {isBulk && (
          <Callout intent="primary" className="hl-mb-sm" icon="selection">
            This Action will run on {bulkCount} selected objects (same parameters for each).
          </Callout>
        )}
        {bulkCapWarning && (
          <Callout intent="warning" className="hl-mb-sm">
            {bulkCapWarning}
          </Callout>
        )}
        <p className="hl-text-muted">
          {action?.risk_level === "high"
            ? isBulk
              ? "High-risk Action — each object creates its own pending approval (not applied immediately)."
              : "This is a high-risk Action — it will create a pending approval, not apply immediately."
            : isBulk
              ? "Low/medium risk — each object is applied immediately in sequence."
              : "This Action applies immediately."}
        </p>
        <ActionParameterFields
          parameters={action?.parameters ?? []}
          values={parameters}
          onChange={onParametersChange}
          sections={action?.sections}
          currentObjectId={currentObjectId}
          currentObject={currentObject}
        />
        <InputGroup placeholder="Reason" value={reason} onChange={(e) => onReasonChange(e.target.value)} />
      </DialogBody>
      <DialogFooter
        actions={
          <Button intent="primary" loading={loading} onClick={onSubmit} disabled={!reason.trim()}>
            {isBulk ? `Run on ${bulkCount}` : "Submit"}
          </Button>
        }
      />
    </Dialog>
  );
}
