import { Tag } from "@blueprintjs/core";
import { Link } from "@tanstack/react-router";
import { FormattedValue } from "../common/PropertyFormat";
import type {
  ConditionalFormatRule,
  ObjectType,
  PropertyFormatRule,
  SharedPropertyType,
} from "../../api/knowledge";
import {
  OBJECT_METADATA_KEYS,
  buildExplorerColumnKeys,
  fkTargetForField,
  humanizeApiName,
  inferredFormatRule,
  preferTitleColumnFirst,
  type RelatedLink,
} from "./objectExplorerUtils";
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
  maskedFields,
  fkFieldTargets,
  formatsBySourceKey,
  conditionalFormatsBySourceKey,
  principalsByUrn,
  sharedPropertyTypes = [],
  relatedLinks = [],
  onOpenLink,
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
  relatedLinks?: RelatedLink[];
  onOpenLink?: (linkName?: string) => void;
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

  const hasTags = durableImplements.length > 0;

  return (
    <div className="hl-oe-ov-overview">
      {hasTags && (
        <div className="hl-tag-row hl-mb-sm">
          {durableImplements.map((iface) => (
            <Tag key={iface} minimal>
              {humanizeApiName(iface)}
            </Tag>
          ))}
        </div>
      )}

      <h4 className="hl-section-title hl-mb-sm">{usingFallback ? "Key properties" : "Prominent properties"}</h4>

      {keys.length === 0 ? (
        <p className="hl-text-muted">No properties to preview.</p>
      ) : (
        <dl className="hl-oe-ov-prominent-grid">
          {keys.map((key) => {
            const value = object[key];
            const fkTarget = fkTargetForField(fkFieldTargets, key);
            const typeRule = resolveDisplayTypeRule(
              resolvePropertyTypeRule(key, objectType?.property_types, objectType?.property_mapping),
              sharedPropertyTypes,
            );
            const style = applyConditionalStyle(conditionalFormatsBySourceKey.get(key), object, value);
            const formatRule =
              inferredFormatRule(
                formatsBySourceKey.get(key) ?? formatsBySourceKey.get(camelToSnake(key)),
                value,
              );
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
                      className="hl-link-accent"
                    >
                      {String(value)} → {fkTarget}
                    </Link>
                  ) : (
                    <FormattedValue
                      rule={formatRule}
                      value={value}
                      principalsByUrn={principalsByUrn}
                      typeRule={typeRule}
                      compact={false}
                    />
                  )}
                </dd>
              </div>
            );
          })}
        </dl>
      )}

      {relatedLinks.length > 0 && onOpenLink && (
        <div className="hl-mt-md">
          <div className="hl-flex-between hl-items-center">
            <h4 className="hl-section-title" style={{ margin: 0 }}>
              Links
            </h4>
            <button type="button" className="hl-link-accent hl-oe-ov-text-btn" onClick={() => onOpenLink()}>
              View all ({relatedLinks.length})
            </button>
          </div>
          <div className="hl-tag-row hl-mt-sm">
            {relatedLinks.slice(0, 8).map((link, i) => (
              <Tag
                key={`${link.linkName}-${i}`}
                minimal
                interactive
                intent={link.visibility === "prominent" ? "primary" : "none"}
                icon="link"
                onClick={() => onOpenLink(link.linkName)}
              >
                {link.pluralLabel || link.label}
              </Tag>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
