import { Dialog, DialogBody, Tag } from "@blueprintjs/core";
import { Link } from "@tanstack/react-router";
import type { ObjectType } from "../../api/knowledge";
import { comparePropertyKeys, valuesDiffer } from "./csvExport";
import { titleOf } from "./objectExplorerUtils";

/** Side-by-side property compare for two selected objects. */
export function CompareObjectsDialog({
  isOpen,
  onClose,
  objectTypeName,
  objectType,
  left,
  right,
}: {
  isOpen: boolean;
  onClose: () => void;
  objectTypeName: string;
  objectType?: ObjectType | null;
  left: Record<string, unknown> | null;
  right: Record<string, unknown> | null;
}) {
  if (!left || !right) {
    return (
      <Dialog isOpen={isOpen} onClose={onClose} title="Compare objects">
        <DialogBody>
          <p className="hl-text-muted">Select exactly two objects to compare.</p>
        </DialogBody>
      </Dialog>
    );
  }

  const leftId = String(left.id);
  const rightId = String(right.id);
  const keys = comparePropertyKeys([left, right]);
  const diffCount = keys.filter((k) => valuesDiffer(left[k], right[k])).length;

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title={`Compare · ${titleOf(left, objectType)} vs ${titleOf(right, objectType)}`}
      style={{ width: "min(920px, 96vw)" }}
    >
      <DialogBody>
        <div className="hl-flex-between hl-mb-sm">
          <Tag minimal intent={diffCount > 0 ? "warning" : "success"}>
            {diffCount} differing propert{diffCount === 1 ? "y" : "ies"}
          </Tag>
          <div className="hl-flex-row hl-gap-sm">
            <Link to="/objects/$type/$id" params={{ type: objectTypeName, id: leftId }} className="hl-link-accent">
              Open left
            </Link>
            <Link to="/objects/$type/$id" params={{ type: objectTypeName, id: rightId }} className="hl-link-accent">
              Open right
            </Link>
          </div>
        </div>

        <div className="hl-oe-compare-scroll">
          <table className="hl-data-table hl-data-table-compact hl-oe-compare-table">
            <thead>
              <tr>
                <th>Property</th>
                <th>
                  <span className="hl-mono">{objectTypeName}/{leftId}</span>
                </th>
                <th>
                  <span className="hl-mono">{objectTypeName}/{rightId}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => {
                const diff = valuesDiffer(left[key], right[key]);
                return (
                  <tr key={key} data-diff={diff ? "true" : undefined} className="hl-data-table-row">
                    <td className="hl-mono">{key}</td>
                    <td className={diff ? "hl-oe-compare-diff" : undefined}>{formatCell(left[key])}</td>
                    <td className={diff ? "hl-oe-compare-diff" : undefined}>{formatCell(right[key])}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </DialogBody>
    </Dialog>
  );
}

function formatCell(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
