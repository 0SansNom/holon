import type { ReactNode } from "react";
import { Button, Card } from "@blueprintjs/core";

export function OntologyTabHeader({
  description,
  createLabel,
  onCreate,
  createDisabled,
  trailing,
}: {
  description: ReactNode;
  createLabel?: string;
  onCreate?: () => void;
  createDisabled?: boolean;
  trailing?: ReactNode;
}) {
  return (
    <div className="hl-flex-between hl-mb-sm">
      <p className="hl-ontology-tab-desc">{description}</p>
      <div className="hl-flex-row">
        {trailing}
        {createLabel && onCreate && (
          <Button intent="primary" icon="add" disabled={createDisabled} onClick={onCreate}>
            {createLabel}
          </Button>
        )}
      </div>
    </div>
  );
}

export function RegistryCard({
  name,
  onEdit,
  onBranch,
  children,
}: {
  name: string;
  onEdit?: () => void;
  onBranch?: () => void;
  children?: ReactNode;
}) {
  return (
    <Card>
      <div className="hl-registry-card-header">
        <strong className="hl-registry-card-title" title={name}>
          {name}
        </strong>
        {(onEdit || onBranch) && (
          <div className="hl-flex-row">
            {onEdit && <Button minimal small icon="edit" onClick={onEdit} />}
            {onBranch && <Button minimal small icon="git-branch" onClick={onBranch} />}
          </div>
        )}
      </div>
      {children}
    </Card>
  );
}
