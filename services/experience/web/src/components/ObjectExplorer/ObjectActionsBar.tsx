import { Button, Tag } from "@blueprintjs/core";
import type { ActionDefinition } from "../../api/knowledge";
import { hasTypeClass } from "../Ontology/typeClassUtils";

export function ObjectActionsBar({
  actions,
  onSelect,
  title = "Actions",
  subtitle,
  variant = "section",
  maxHeaderActions = 4,
}: {
  actions: ActionDefinition[];
  onSelect: (actionName: string) => void;
  title?: string;
  subtitle?: string;
  /** `header` = compact buttons for Object View chrome (no section title). */
  variant?: "section" | "header";
  maxHeaderActions?: number;
}) {
  const visible = actions.filter((a) => !hasTypeClass(a.type_classes, "hubble-oe", "hide-action"));
  if (visible.length === 0) return null;

  const shown = variant === "header" ? visible.slice(0, maxHeaderActions) : visible;
  const overflow = variant === "header" ? visible.length - shown.length : 0;

  const buttons = (
    <div className="hl-flex-row hl-gap-sm" style={{ flexWrap: "wrap" }}>
      {shown.map((a) => (
        <Button
          key={a.name}
          small={variant === "header"}
          intent={a.risk_level === "high" ? "danger" : "primary"}
          onClick={() => {
            onSelect(a.name);
          }}
        >
          {a.name.includes(".") ? a.name.split(".").slice(1).join(".") : a.name}
          {variant === "section" && (
            <Tag minimal className="hl-ml-xs">
              {a.risk_level}
            </Tag>
          )}
        </Button>
      ))}
      {overflow > 0 && (
        <Tag minimal>
          +{overflow} more on Overview
        </Tag>
      )}
    </div>
  );

  if (variant === "header") return buttons;

  return (
    <div className="hl-section">
      <h4 className="hl-section-title">{title}</h4>
      {subtitle && <p className="hl-text-muted-sm hl-mb-sm">{subtitle}</p>}
      {buttons}
    </div>
  );
}
