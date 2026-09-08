import { Button, Tag } from "@blueprintjs/core";
import { PredicateFilterRows } from "../Ontology/PredicateFilterRows";
import {
  buildPredicateDefinition,
  formatPredicateChip,
  type PredicateFormRow,
} from "../Ontology/objectSetPredicates";

/** Point-and-click property filters on Object Explorer (Object Set semantics, client-side). */
export function TableFilterBar({
  propertyKeys,
  predicates,
  onChange,
  onClear,
}: {
  propertyKeys: string[];
  predicates: PredicateFormRow[];
  onChange: (next: PredicateFormRow[]) => void;
  onClear: () => void;
}) {
  const active = buildPredicateDefinition(predicates).all;
  const empty = predicates.length === 0;

  return (
    <div className={`hl-oe-filter-bar${empty ? " hl-oe-filter-bar--empty" : ""}`}>
      {empty ? (
        <Button
          small
          minimal
          icon="filter"
          disabled={propertyKeys.length === 0}
          onClick={() => onChange([{ property: propertyKeys[0] ?? "", op: "eq", value: "" }])}
        >
          Add filter
        </Button>
      ) : (
        <>
          <div className="hl-flex-between hl-items-center hl-mb-sm">
            <div className="hl-section-title" style={{ margin: 0 }}>
              Filters
            </div>
            <div className="hl-flex-row hl-gap-sm hl-items-center">
              {active.length > 0 && (
                <Tag minimal intent="primary" icon="filter">
                  {active.length} active
                </Tag>
              )}
              <Button minimal small icon="cross" onClick={onClear}>
                Clear
              </Button>
            </div>
          </div>
          <PredicateFilterRows
            predicates={predicates}
            propertyKeys={propertyKeys}
            onChange={onChange}
            addLabel="Add filter"
            allowEmpty
          />
          {active.length > 0 && (
            <div className="hl-tag-row hl-mt-sm">
              {active.map((pred, i) => (
                <Tag key={`${pred.property}-${pred.op}-${i}`} minimal className="hl-mono">
                  {formatPredicateChip(pred)}
                </Tag>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
