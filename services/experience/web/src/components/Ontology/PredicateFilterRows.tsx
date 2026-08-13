import { Button, HTMLSelect, InputGroup } from "@blueprintjs/core";
import { OBJECT_SET_OPS, type PredicateFormRow } from "./objectSetPredicates";

/** Shared AND-predicate editor used by Object Sets admin and Object Explorer filters. */
export function PredicateFilterRows({
  predicates,
  propertyKeys,
  onChange,
  addLabel = "Add predicate",
  allowEmpty = false,
}: {
  predicates: PredicateFormRow[];
  propertyKeys: string[];
  onChange: (next: PredicateFormRow[]) => void;
  addLabel?: string;
  /** When true, the last row can be removed (OE ad-hoc filters). */
  allowEmpty?: boolean;
}) {
  return (
    <div className="hl-flex-col hl-gap-sm">
      {predicates.map((pred, index) => (
        <div key={index} className="hl-predicate-row">
          <HTMLSelect
            value={pred.property}
            onChange={(e) => {
              const next = [...predicates];
              next[index] = { ...pred, property: e.target.value };
              onChange(next);
            }}
          >
            <option value="">Property…</option>
            {propertyKeys.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </HTMLSelect>
          <HTMLSelect
            value={pred.op}
            onChange={(e) => {
              const next = [...predicates];
              next[index] = { ...pred, op: e.target.value };
              onChange(next);
            }}
          >
            {OBJECT_SET_OPS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </HTMLSelect>
          <InputGroup
            value={pred.value}
            placeholder={pred.op === "in" ? "a, b, c" : "value"}
            onChange={(e) => {
              const next = [...predicates];
              next[index] = { ...pred, value: e.target.value };
              onChange(next);
            }}
          />
          <Button
            minimal
            icon="cross"
            disabled={!allowEmpty && predicates.length <= 1}
            onClick={() => onChange(predicates.filter((_, i) => i !== index))}
            aria-label="Remove predicate"
          />
        </div>
      ))}
      <Button
        small
        minimal
        icon="plus"
        onClick={() =>
          onChange([...predicates, { property: propertyKeys[0] ?? "", op: "eq", value: "" }])
        }
      >
        {addLabel}
      </Button>
    </div>
  );
}
