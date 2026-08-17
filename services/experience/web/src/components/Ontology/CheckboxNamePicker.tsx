import { Checkbox } from "@blueprintjs/core";

/** Multi-select checkbox grid used by Interfaces create/edit forms. */
export function CheckboxNamePicker({
  options,
  values,
  onChange,
  emptyHint,
  optionLabel,
}: {
  options: string[];
  values: string[];
  onChange: (next: string[]) => void;
  emptyHint?: string;
  optionLabel?: (name: string) => string;
}) {
  const selected = new Set(values);

  function toggle(name: string) {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onChange([...next]);
  }

  if (options.length === 0) {
    return <p className="hl-text-muted-sm">{emptyHint ?? "Nothing to pick yet."}</p>;
  }

  return (
    <div className="hl-ot-draft-check-grid">
      {options.map((name) => (
        <Checkbox
          key={name}
          label={optionLabel ? optionLabel(name) : name}
          checked={selected.has(name)}
          onChange={() => toggle(name)}
        />
      ))}
    </div>
  );
}
