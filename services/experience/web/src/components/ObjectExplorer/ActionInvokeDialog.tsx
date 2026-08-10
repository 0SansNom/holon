import { Button, Dialog, DialogBody, DialogFooter, InputGroup } from "@blueprintjs/core";
import type { ActionDefinition } from "../../api/knowledge";
import { ActionParameterFields } from "../common/ActionParameterFields";

export function ActionInvokeDialog({
  action,
  reason,
  parameters,
  loading,
  onReasonChange,
  onParametersChange,
  onClose,
  onSubmit,
}: {
  action: ActionDefinition | undefined;
  reason: string;
  parameters: Record<string, unknown>;
  loading: boolean;
  onReasonChange: (reason: string) => void;
  onParametersChange: (parameters: Record<string, unknown>) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  return (
    <Dialog isOpen={action !== undefined} onClose={onClose} title={action?.name ?? ""}>
      <DialogBody>
        <p className="hl-text-muted">
          {action?.risk_level === "high"
            ? "This is a high-risk Action — it will create a pending approval, not apply immediately."
            : "This Action applies immediately."}
        </p>
        <ActionParameterFields
          parameters={action?.parameters ?? []}
          values={parameters}
          onChange={onParametersChange}
          sections={action?.sections}
        />
        <InputGroup placeholder="Reason" value={reason} onChange={(e) => onReasonChange(e.target.value)} />
      </DialogBody>
      <DialogFooter
        actions={
          <Button intent="primary" loading={loading} onClick={onSubmit}>
            Submit
          </Button>
        }
      />
    </Dialog>
  );
}
