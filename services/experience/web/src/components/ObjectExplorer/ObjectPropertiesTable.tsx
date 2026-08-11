import { Link } from "@tanstack/react-router";
import { FormattedValue } from "../common/PropertyFormat";
import { applyConditionalStyle } from "../common/propertyFormatUtils";
import type { ConditionalFormatRule, ObjectType, PropertyFormatRule } from "../../api/knowledge";
import { OBJECT_METADATA_KEYS } from "./objectExplorerUtils";
import { isPropertyHidden, sortPropertiesByVisibility } from "../Ontology/propertyEditorUtils";

export function ObjectPropertiesTable({
  object,
  objectType,
  maskedFields,
  fkFieldTargets,
  formatsBySourceKey,
  conditionalFormatsBySourceKey,
  principalsByUrn,
}: {
  object: Record<string, unknown>;
  objectType?: ObjectType | null;
  maskedFields: string[];
  fkFieldTargets: Map<string, string>;
  formatsBySourceKey: Map<string, PropertyFormatRule>;
  conditionalFormatsBySourceKey: Map<string, ConditionalFormatRule[]>;
  principalsByUrn: Map<string, string>;
}) {
  const keys = sortPropertiesByVisibility(
    Object.keys(object).filter((key) => !OBJECT_METADATA_KEYS.has(key)),
    objectType?.property_types,
    objectType?.property_mapping,
  ).filter((key) => !isPropertyHidden(key, objectType?.property_types, objectType?.property_mapping));

  return (
    <div className="hl-panel hl-mt-md">
      <table className="hl-properties-table">
        <tbody>
          {keys.map((key) => {
            const value = object[key];
            const fkTargetType = fkFieldTargets.get(key);
            return (
              <tr key={key} className="hl-properties-row">
                <td className="hl-properties-key">{key}</td>
                <td
                  className="hl-properties-value"
                  style={applyConditionalStyle(conditionalFormatsBySourceKey.get(key), object, value)}
                >
                  {maskedFields.includes(key) ? (
                    <span className="hl-masked-field">forbidden — masked by permission</span>
                  ) : value !== null && fkTargetType ? (
                    <Link
                      to="/objects/$type/$id"
                      params={{ type: fkTargetType, id: String(value) }}
                      className="hl-mono hl-link-accent"
                    >
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
