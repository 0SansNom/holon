import { Button, Tag } from "@blueprintjs/core";
import type { ActionDefinition } from "../../api/knowledge";

export function ObjectActionsBar({
  actions,
  onSelect,
}: {
  actions: ActionDefinition[];
  onSelect: (actionName: string) => void;
}) {
  if (actions.length === 0) return null;

  return (
    <div className="hl-section">
      <h4 className="hl-section-title">Actions</h4>
      <div className="hl-flex-row hl-gap-sm">
        {actions.map((a) => (
          <Button
            key={a.name}
            intent={a.risk_level === "high" ? "danger" : "primary"}
            onClick={() => {
              onSelect(a.name);
            }}
          >
            {a.name.split(".")[1]}
            <Tag minimal className="hl-ml-xs">
              {a.risk_level}
            </Tag>
          </Button>
        ))}
      </div>
    </div>
  );
}
