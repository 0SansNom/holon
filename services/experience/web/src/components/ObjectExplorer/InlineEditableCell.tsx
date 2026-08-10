import { useState } from "react";
import { Icon, InputGroup } from "@blueprintjs/core";
import type { ActionDefinition } from "../../api/knowledge";
import { coerce } from "../common/actionParameterUtils";
import { FormattedValue } from "../common/PropertyFormat";

export function InlineEditableCell({
  value,
  action,
  onSubmit,
  baseType,
}: {
  value: unknown;
  action: ActionDefinition;
  onSubmit: (value: unknown) => void;
  baseType: string | undefined;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  if (!editing) {
    return (
      <span
        className="hl-inline-editable-cell"
        onClick={(e) => {
          e.stopPropagation();
          setDraft(value !== null && value !== undefined ? String(value) : "");
          setEditing(true);
        }}
      >
        <FormattedValue value={value} rule={undefined} />
        <Icon icon="edit" size={11} className="hl-inline-edit-affordance" />
      </span>
    );
  }

  function commit() {
    setEditing(false);
    if (draft === (value !== null && value !== undefined ? String(value) : "")) return;
    onSubmit(coerce(draft, baseType));
  }

  return (
    <InputGroup
      autoFocus
      small
      value={draft}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setEditing(false);
      }}
      title={`${action.name} — Enter to apply, Esc to cancel`}
    />
  );
}
