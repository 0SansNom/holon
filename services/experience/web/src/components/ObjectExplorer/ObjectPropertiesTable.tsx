import { Link } from "@tanstack/react-router";
import { FormattedValue } from "../common/PropertyFormat";
import { applyConditionalStyle } from "../common/propertyFormatUtils";
import type { ConditionalFormatRule, PropertyFormatRule } from "../../api/knowledge";
import { OBJECT_METADATA_KEYS } from "./objectExplorerUtils";

export function ObjectPropertiesTable({
  object,
  maskedFields,
  fkFieldTargets,
  formatsBySourceKey,
  conditionalFormatsBySourceKey,
  principalsByUrn,
}: {
  object: Record<string, unknown>;
  maskedFields: string[];
  fkFieldTargets: Map<string, string>;
  formatsBySourceKey: Map<string, PropertyFormatRule>;
  conditionalFormatsBySourceKey: Map<string, ConditionalFormatRule[]>;
  principalsByUrn: Map<string, string>;
}) {
  return (
    <div className="hl-panel hl-mt-md">
      <table className="hl-properties-table">
        <tbody>
          {Object.entries(object)
            .filter(([key]) => !OBJECT_METADATA_KEYS.has(key))
            .map(([key, value]) => {
              const fkTargetType = fkFieldTargets.get(key);
              return (
                <tr key={key} className="hl-properties-row">
                  <td className="hl-properties-key">{key}</td>
                  <td className="hl-properties-value" style={applyConditionalStyle(conditionalFormatsBySourceKey.get(key), object, value)}>
                    {maskedFields.includes(key) ? (
                      <span className="hl-masked-field">forbidden — masked by permission</span>
                    ) : value !== null && fkTargetType ? (
                      <Link to="/objects/$type/$id" params={{ type: fkTargetType, id: String(value) }} className="hl-mono hl-link-accent">
                        {String(value)} → {fkTargetType}
                      </Link>
                    ) : (
                      <FormattedValue rule={formatsBySourceKey.get(key)} value={value} principalsByUrn={principalsByUrn} />
                    )}
                  </td>
                </tr>
              );
            })}
        </tbody>
      </table>
    </div>
  );
}
