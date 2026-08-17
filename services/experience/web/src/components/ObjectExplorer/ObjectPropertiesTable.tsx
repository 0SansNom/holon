import { Link } from "@tanstack/react-router";
import { Tag } from "@blueprintjs/core";
import { FormattedValue } from "../common/PropertyFormat";
import { applyConditionalStyle } from "../common/propertyFormatUtils";
import type {
  ActionDefinition,
  ConditionalFormatRule,
  ObjectType,
  PropertyFormatRule,
  SharedPropertyType,
} from "../../api/knowledge";
import { OBJECT_METADATA_KEYS } from "./objectExplorerUtils";
import { InlineEditableCell } from "./InlineEditableCell";
import {
  isPropertyHidden,
  lookupSharedPropertyForKey,
  effectivePropertyAliases,
  resolveDisplayTypeRule,
  resolvePropertyTypeRule,
  sortPropertiesByVisibility,
} from "../Ontology/propertyEditorUtils";
import { hasTypeClass } from "../Ontology/typeClassUtils";

export function ObjectPropertiesTable({
  object,
  objectType,
  maskedFields,
  fkFieldTargets,
  formatsBySourceKey,
  conditionalFormatsBySourceKey,
  principalsByUrn,
  sharedPropertyTypes = [],
  inlineEditableBySourceKey,
  inlineBaseTypeBySourceKey,
  onInlineEdit,
}: {
  object: Record<string, unknown>;
  objectType?: ObjectType | null;
  maskedFields: string[];
  fkFieldTargets: Map<string, string>;
  formatsBySourceKey: Map<string, PropertyFormatRule>;
  conditionalFormatsBySourceKey: Map<string, ConditionalFormatRule[]>;
  principalsByUrn: Map<string, string>;
  sharedPropertyTypes?: SharedPropertyType[];
  inlineEditableBySourceKey?: Map<string, ActionDefinition>;
  inlineBaseTypeBySourceKey?: Map<string, string | undefined>;
  onInlineEdit?: (action: ActionDefinition, value: unknown) => void;
}) {
  const keys = sortPropertiesByVisibility(
    Object.keys(object).filter((key) => !OBJECT_METADATA_KEYS.has(key)),
    objectType?.property_types,
    objectType?.property_mapping,
    sharedPropertyTypes,
  ).filter((key) => !isPropertyHidden(key, objectType?.property_types, objectType?.property_mapping, sharedPropertyTypes));

  const keywordKeys = keys.filter((key) => {
    const typeRule = resolveDisplayTypeRule(
      resolvePropertyTypeRule(key, objectType?.property_types, objectType?.property_mapping),
      sharedPropertyTypes,
    );
    return typeRule?.render_hints?.includes("keywords");
  });

  function renderValue(key: string, value: unknown) {
    const fkTargetType = fkFieldTargets.get(key);
    const typeRule = resolveDisplayTypeRule(
      resolvePropertyTypeRule(key, objectType?.property_types, objectType?.property_mapping),
      sharedPropertyTypes,
    );
    if (maskedFields.includes(key)) {
      return <span className="hl-masked-field">forbidden — masked by permission</span>;
    }
    const inlineAction = inlineEditableBySourceKey?.get(key);
    if (inlineAction && onInlineEdit && !fkTargetType) {
      return (
        <InlineEditableCell
          value={value}
          action={inlineAction}
          baseType={inlineBaseTypeBySourceKey?.get(key)}
          onSubmit={(next) => onInlineEdit(inlineAction, next)}
        />
      );
    }
    if (value !== null && fkTargetType) {
      return (
        <Link
          to="/objects/$type/$id"
          params={{ type: fkTargetType, id: String(value) }}
          className="hl-mono hl-link-accent"
        >
          {String(value)} → {fkTargetType}
        </Link>
      );
    }
    if (hasTypeClass(typeRule?.type_classes, "hubble", "media_url") && typeof value === "string" && value) {
      return (
        <a href={value} target="_blank" rel="noreferrer" className="hl-media-url">
          <img src={value} alt={key} className="hl-object-media-thumb" />
        </a>
      );
    }
    if (typeRule?.render_hints?.includes("long_text") && typeof value === "string") {
      return <pre className="hl-long-text">{value}</pre>;
    }
    return (
      <FormattedValue
        rule={formatsBySourceKey.get(key)}
        value={value}
        principalsByUrn={principalsByUrn}
        typeRule={typeRule}
        compact={false}
      />
    );
  }

  return (
    <div className="hl-panel hl-mt-md">
      {keywordKeys.length > 0 && (
        <div className="hl-keywords-section">
          <div className="hl-section-title hl-mb-sm">Keywords</div>
          <div className="hl-tag-row">
            {keywordKeys.map((key) => {
              const value = object[key];
              if (value === null || value === undefined || value === "") return null;
              return (
                <Tag key={key} minimal intent="primary" title={key}>
                  {String(value)}
                </Tag>
              );
            })}
          </div>
        </div>
      )}
      <table className="hl-properties-table">
        <tbody>
          {keys.map((key) => {
            const value = object[key];
            const typeRule = resolveDisplayTypeRule(
              resolvePropertyTypeRule(key, objectType?.property_types, objectType?.property_mapping),
              sharedPropertyTypes,
            );
            const aliases = effectivePropertyAliases(
              key,
              objectType?.property_types,
              objectType?.property_mapping,
              sharedPropertyTypes,
            );
            return (
              <tr key={key} className="hl-properties-row">
                <td className="hl-properties-key">
                  <span className="hl-flex-row hl-items-center hl-gap-xs">
                    {key}
                    {lookupSharedPropertyForKey(
                      key,
                      objectType?.property_types,
                      objectType?.property_mapping,
                      sharedPropertyTypes,
                    ) && <Tag minimal icon="globe" title="Shared property" />}
                    {typeRule?.lifecycle_status && typeRule.lifecycle_status !== "active" && (
                      <Tag
                        minimal
                        intent={typeRule.lifecycle_status === "deprecated" ? "warning" : "none"}
                        title="Property lifecycle"
                      >
                        {typeRule.lifecycle_status}
                      </Tag>
                    )}
                  </span>
                  {aliases.length > 0 && (
                    <div className="hl-tag-row hl-mt-xs">
                      {aliases.map((alias) => (
                        <Tag key={alias} minimal className="hl-text-muted-sm">
                          {alias}
                        </Tag>
                      ))}
                    </div>
                  )}
                </td>
                <td
                  className="hl-properties-value"
                  style={applyConditionalStyle(conditionalFormatsBySourceKey.get(key), object, value)}
                >
                  {renderValue(key, value)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
