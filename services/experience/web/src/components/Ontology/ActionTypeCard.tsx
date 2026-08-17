import { Button, Tag } from "@blueprintjs/core";
import { useNavigate } from "@tanstack/react-router";
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
  const navigate = useNavigate();
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
        <Tag minimal intent={actionType.lifecycle_status === "deprecated" ? "warning" : "none"}>
          {actionType.lifecycle_status ?? "experimental"}
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
        {(actionType.type_classes ?? []).map((tc) => (
          <Tag key={tc} minimal>
            {tc}
          </Tag>
        ))}
      </div>
      {actionType.description && <p className="hl-card-desc">{actionType.description}</p>}
      <div className="hl-card-actions">
        <Button
          small
          minimal
          icon="document-open"
          onClick={() =>
            void navigate({
              to: "/ontology/action-types/$name",
              params: { name: actionType.name },
            })
          }
        >
          Open
        </Button>
      </div>
    </RegistryCard>
  );
}
