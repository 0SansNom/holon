import { Tag } from "@blueprintjs/core";
import type { ActionType } from "../../api/knowledge";
import { RegistryCard } from "./OntologyTabLayout";

export function ActionTypeCard({
  actionType,
  onEdit,
  onBranch,
}: {
  actionType: ActionType;
  onEdit: () => void;
  onBranch: () => void;
}) {
  return (
    <RegistryCard name={actionType.name} onEdit={onEdit} onBranch={onBranch}>
      <div className="hl-tag-row hl-mt-xs">
        {actionType.target_interface ? (
          <Tag minimal icon="layers">
            {actionType.target_interface}
          </Tag>
        ) : (
          <Tag minimal>{actionType.target_object_type}</Tag>
        )}
        <Tag minimal intent={actionType.risk_level === "high" ? "warning" : "none"}>
          {actionType.risk_level} risk
        </Tag>
        {actionType.writeback_dataset && (
          <Tag minimal icon="cloud-upload">
            writes back
          </Tag>
        )}
        {actionType.edit_function && (
          <Tag minimal icon="function">
            function-backed
          </Tag>
        )}
        {actionType.sections && actionType.sections.length > 0 && (
          <Tag minimal icon="group-objects">
            {actionType.sections.length} section{actionType.sections.length === 1 ? "" : "s"}
          </Tag>
        )}
      </div>
      {actionType.description && <p className="hl-card-desc">{actionType.description}</p>}
    </RegistryCard>
  );
}
