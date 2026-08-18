import { Tag } from "@blueprintjs/core";
import { Link } from "@tanstack/react-router";
import { FormattedValue } from "../common/PropertyFormat";
import type { ConditionalFormatRule, ObjectType, PropertyFormatRule, SharedPropertyType } from "../../api/knowledge";
import { OBJECT_METADATA_KEYS, buildExplorerColumnKeys, humanizeApiName, preferTitleColumnFirst } from "./objectExplorerUtils";
import { isEphemeralTestName } from "../Ontology/ephemeralResources";
import {
  effectivePropertyVisibility,
  resolveDisplayTypeRule,
  resolvePropertyTypeRule,
  sortPropertiesByVisibility,
} from "../Ontology/propertyEditorUtils";
import { applyConditionalStyle, camelToSnake } from "../common/propertyFormatUtils";

/** Prominent (or fallback) properties pinned under the Object View title — Foundry Overview chrome. */
export function ObjectViewOverview({
  object,
  objectType,
  objectTypeName,
  maskedFields,
  fkFieldTargets,
  formatsBySourceKey,
  conditionalFormatsBySourceKey,
  principalsByUrn,
  sharedPropertyTypes = [],
  maxFallback = 6,
}: {
  object: Record<string, unknown>;
  objectType?: ObjectType | null;
  objectTypeName: string;
  maskedFields: string[];
  fkFieldTargets: Map<string, string>;
  formatsBySourceKey: Map<string, PropertyFormatRule>;
  conditionalFormatsBySourceKey: Map<string, ConditionalFormatRule[]>;
  principalsByUrn: Map<string, string>;
  sharedPropertyTypes?: SharedPropertyType[];
  maxFallback?: number;
}) {
  const visible = (key: string) =>
    effectivePropertyVisibility(key, objectType?.property_types, objectType?.property_mapping, sharedPropertyTypes) !==
    "hidden";

  const allKeys = sortPropertiesByVisibility(
    Object.keys(object).filter((k) => !OBJECT_METADATA_KEYS.has(k) && visible(k)),
    objectType?.property_types,
    objectType?.property_mapping,
    sharedPropertyTypes,
  );

  const prominent = allKeys.filter(
    (k) =>
      effectivePropertyVisibility(k, objectType?.property_types, objectType?.property_mapping, sharedPropertyTypes) ===
      "prominent",
  );
  const fallbackKeys = preferTitleColumnFirst(
    buildExplorerColumnKeys(objectType, object).filter((k) => !OBJECT_METADATA_KEYS.has(k) && visible(k)),
    objectType,
  ).filter((k) => k !== "id" && k !== objectType?.primary_key);
  const keys = prominent.length > 0 ? prominent : fallbackKeys.slice(0, maxFallback);
  const usingFallback = prominent.length === 0;
  const durableImplements = (objectType?.implements ?? []).filter((iface) => !isEphemeralTestName(iface));

  const hasTags = Boolean(objectType?.classification) || durableImplements.length > 0;

  return (
    <div className="hl-oe-ov-overview">
      {hasTags && (
        <div className="hl-tag-row hl-mb-sm">
          {objectType?.classification && <Tag minimal>{objectType.classification}</Tag>}
          {durableImplements.map((iface) => (
            <Tag key={iface} minimal>
              {humanizeApiName(iface)}
            </Tag>
          ))}
        </div>
      )}

      <div className="hl-flex-between hl-items-center hl-mb-sm">
        <h4 className="hl-section-title" style={{ margin: 0 }}>
          {usingFallback ? "Key properties" : "Prominent properties"}
        </h4>
        <span className="hl-text-muted-sm">
          {objectTypeName}
          {object.id != null ? ` · ${String(object.id)}` : ""}
        </span>
      </div>

      {keys.length === 0 ? (
        <p className="hl-text-muted">No properties to preview.</p>
      ) : (
        <dl className="hl-oe-ov-prominent-grid">
          {keys.map((key) => {
            const value = object[key];
            const fkTarget = fkFieldTargets.get(key);
            const typeRule = resolveDisplayTypeRule(
              resolvePropertyTypeRule(key, objectType?.property_types, objectType?.property_mapping),
              sharedPropertyTypes,
            );
            const style = applyConditionalStyle(conditionalFormatsBySourceKey.get(key), object, value);
            return (
              <div key={key} className="hl-oe-ov-prominent-item" style={style}>
                <dt className="hl-text-muted-sm" title={key}>
                  {humanizeApiName(key)}
                </dt>
                <dd>
                  {maskedFields.includes(key) ? (
                    <span className="hl-masked-field">masked</span>
                  ) : value != null && fkTarget ? (
                    <Link
                      to="/objects/$type/$id"
                      params={{ type: fkTarget, id: String(value) }}
                      className="hl-link-accent hl-mono"
                    >
                      {String(value)}
                    </Link>
                  ) : (
                    <FormattedValue
                      rule={formatsBySourceKey.get(key) ?? formatsBySourceKey.get(camelToSnake(key))}
                      value={value}
                      principalsByUrn={principalsByUrn}
                      typeRule={typeRule}
                      compact
                    />
                  )}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
    </div>
  );
}
