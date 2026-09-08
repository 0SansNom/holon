import { Button, Callout, Card } from "@blueprintjs/core";
import { useNavigate } from "@tanstack/react-router";
import type {
  ActionDefinition,
  ConditionalFormatRule,
  ObjectType,
  PropertyFormatRule,
  SharedPropertyType,
  TimelineEvent,
} from "../../api/knowledge";
import { FormattedValue } from "../common/PropertyFormat";
import { camelToSnake } from "../common/propertyFormatUtils";
import {
  resolveDisplayTypeRule,
  resolvePropertyTypeRule,
} from "../Ontology/propertyEditorUtils";
import { ObjectMediaGallery } from "./ObjectMediaGallery";
import { ObjectPropertiesTable } from "./ObjectPropertiesTable";
import { ObjectTimelinePanel } from "./ObjectTimelinePanel";
import { ObjectViewOverview } from "./ObjectViewOverview";
import { RelatedLinkPanel } from "./RelatedLinkPanel";
import type { ObjectMediaItem } from "./objectMedia";
import type { RelatedLink } from "./objectExplorerUtils";
import {
  resolveIframeUrl,
  widgetKindLabel,
  type ObjectViewTabDef,
  type ObjectViewWidget,
} from "./objectViewDefinition";

export type ObjectViewHostContext = {
  objectTypeName: string;
  objectId: string;
  object: Record<string, unknown>;
  objectType?: ObjectType | null;
  maskedFields: string[];
  fkFieldTargets: Map<string, string>;
  formatsBySourceKey: Map<string, PropertyFormatRule>;
  conditionalFormatsBySourceKey: Map<string, ConditionalFormatRule[]>;
  principalsByUrn: Map<string, string>;
  sharedPropertyTypes: SharedPropertyType[];
  relatedLinks: RelatedLink[];
  mediaItems: ObjectMediaItem[];
  timeline?: TimelineEvent[] | null;
  nextRevertibleId?: number | null;
  reverting?: boolean;
  onRevert?: (invocationId: number) => void;
  inlineEditableBySourceKey?: Map<string, ActionDefinition>;
  inlineBaseTypeBySourceKey?: Map<string, string | undefined>;
  onInlineEdit?: (action: ActionDefinition, value: unknown) => void;
};

export function ObjectViewWidgetHost({
  tab,
  ctx,
}: {
  tab: ObjectViewTabDef;
  ctx: ObjectViewHostContext;
}) {
  if (tab.widgets.length === 0) {
    return <p className="hl-text-muted">No widgets on this tab. Edit the Object View to add some.</p>;
  }
  return (
    <div className="hl-oe-ov-configured-stack">
      {tab.widgets.map((widget) => (
        <ConfiguredWidget key={widget.id} widget={widget} ctx={ctx} />
      ))}
    </div>
  );
}

function ConfiguredWidget({ widget, ctx }: { widget: ObjectViewWidget; ctx: ObjectViewHostContext }) {
  const navigate = useNavigate();
  const title = widget.label || widgetKindLabel(widget.kind);

  if (widget.kind === "overview") {
    return (
      <section className="hl-oe-ov-widget">
        <h4 className="hl-section-title">{title}</h4>
        <ObjectViewOverview
          object={ctx.object}
          objectType={ctx.objectType}
          objectTypeName={ctx.objectTypeName}
          maskedFields={ctx.maskedFields}
          fkFieldTargets={ctx.fkFieldTargets}
          formatsBySourceKey={ctx.formatsBySourceKey}
          conditionalFormatsBySourceKey={ctx.conditionalFormatsBySourceKey}
          principalsByUrn={ctx.principalsByUrn}
          sharedPropertyTypes={ctx.sharedPropertyTypes}
        />
      </section>
    );
  }

  if (widget.kind === "properties") {
    return (
      <section className="hl-oe-ov-widget">
        <h4 className="hl-section-title">{title}</h4>
        <ObjectPropertiesTable
          object={ctx.object}
          objectType={ctx.objectType}
          maskedFields={ctx.maskedFields}
          fkFieldTargets={ctx.fkFieldTargets}
          formatsBySourceKey={ctx.formatsBySourceKey}
          conditionalFormatsBySourceKey={ctx.conditionalFormatsBySourceKey}
          principalsByUrn={ctx.principalsByUrn}
          sharedPropertyTypes={ctx.sharedPropertyTypes}
          inlineEditableBySourceKey={ctx.inlineEditableBySourceKey}
          inlineBaseTypeBySourceKey={ctx.inlineBaseTypeBySourceKey}
          onInlineEdit={ctx.onInlineEdit}
        />
      </section>
    );
  }

  if (widget.kind === "links") {
    return (
      <section className="hl-oe-ov-widget">
        <h4 className="hl-section-title">{title}</h4>
        {ctx.relatedLinks.length === 0 ? (
          <p className="hl-text-muted">No RelationTypes linked to this ObjectType.</p>
        ) : (
          ctx.relatedLinks.map((link, i) => (
            <RelatedLinkPanel
              key={`${link.linkName}-${i}`}
              type={ctx.objectTypeName}
              id={ctx.objectId}
              link={link}
              defaultExpanded={link.visibility === "prominent"}
              writable
            />
          ))
        )}
      </section>
    );
  }

  if (widget.kind === "media") {
    return (
      <section className="hl-oe-ov-widget">
        <h4 className="hl-section-title">{title}</h4>
        <ObjectMediaGallery items={ctx.mediaItems} />
      </section>
    );
  }

  if (widget.kind === "timeline") {
    return (
      <section className="hl-oe-ov-widget">
        <h4 className="hl-section-title">{title}</h4>
        {ctx.timeline ? (
          <ObjectTimelinePanel
            timeline={ctx.timeline}
            principalsByUrn={ctx.principalsByUrn}
            nextRevertibleId={ctx.nextRevertibleId ?? null}
            reverting={ctx.reverting ?? false}
            onRevert={(invocationId) => ctx.onRevert?.(invocationId)}
          />
        ) : (
          <p className="hl-text-muted">No activity on this object yet.</p>
        )}
      </section>
    );
  }

  if (widget.kind === "graph") {
    return (
      <section className="hl-oe-ov-widget">
        <h4 className="hl-section-title">{title}</h4>
        <Callout icon="graph" className="hl-mb-md">
          Explore related instances in the neighborhood graph (2–3 hops).
        </Callout>
        <Button
          intent="primary"
          icon="graph"
          onClick={() =>
            void navigate({
              to: "/objects/$type/$id/graph",
              params: { type: ctx.objectTypeName, id: ctx.objectId },
            })
          }
        >
          Open related instances graph
        </Button>
      </section>
    );
  }

  if (widget.kind === "property_kpi") {
    const key = widget.propertyKey;
    const value = key ? ctx.object[key] : undefined;
    const typeRule = key
      ? resolveDisplayTypeRule(
          resolvePropertyTypeRule(key, ctx.objectType?.property_types, ctx.objectType?.property_mapping),
          ctx.sharedPropertyTypes,
        )
      : undefined;
    return (
      <Card className="hl-oe-ov-widget hl-oe-ov-kpi-card">
        <div className="hl-widget-label">{title}</div>
        <div className="hl-oe-ov-kpi-value">
          {key == null || key === "" ? (
            <span className="hl-text-muted">Pick a property in the Object View editor.</span>
          ) : ctx.maskedFields.includes(key) ? (
            <span className="hl-masked-field">masked</span>
          ) : (
            <FormattedValue
              rule={ctx.formatsBySourceKey.get(key) ?? ctx.formatsBySourceKey.get(camelToSnake(key))}
              value={value}
              principalsByUrn={ctx.principalsByUrn}
              typeRule={typeRule}
            />
          )}
        </div>
        {key ? <div className="hl-text-muted-sm hl-mono">{key}</div> : null}
      </Card>
    );
  }

  if (widget.kind === "iframe") {
    const url = widget.iframeUrl
      ? resolveIframeUrl(widget.iframeUrl, ctx.objectTypeName, ctx.objectId)
      : "";
    return (
      <section className="hl-oe-ov-widget">
        <h4 className="hl-section-title">{title}</h4>
        {!url ? (
          <p className="hl-text-muted">Set an iframe URL in the Object View editor.</p>
        ) : (
          <iframe
            src={url}
            title={title}
            className="hl-plugin-iframe"
            sandbox=""
            referrerPolicy="no-referrer"
            loading="lazy"
          />
        )}
      </section>
    );
  }

  if (widget.kind === "markdown") {
    return (
      <section className="hl-oe-ov-widget">
        <h4 className="hl-section-title">{title}</h4>
        <pre className="hl-oe-ov-note">{widget.markdown?.trim() || "Empty note."}</pre>
      </section>
    );
  }

  return null;
}
