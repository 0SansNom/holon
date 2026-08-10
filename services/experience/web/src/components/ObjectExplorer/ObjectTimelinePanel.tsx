import { Button, Icon, Tag, type IconName } from "@blueprintjs/core";
import type { TimelineEvent } from "../../api/knowledge";
import { FormattedValue } from "../common/PropertyFormat";
import { urnShortName } from "./objectExplorerUtils";

const TIMELINE_ICONS: Record<TimelineEvent["kind"], IconName> = {
  invoked: "tick-circle",
  requested: "time",
  rejected: "cross-circle",
  expired: "outdated",
};

const TIMELINE_LABELS: Record<TimelineEvent["kind"], string> = {
  invoked: "applied",
  requested: "requested",
  rejected: "rejected",
  expired: "expired",
};

export function ObjectTimelinePanel({
  timeline,
  principalsByUrn,
  nextRevertibleId,
  reverting,
  onRevert,
}: {
  timeline: TimelineEvent[];
  principalsByUrn: Map<string, string>;
  nextRevertibleId: number | null;
  reverting: boolean;
  onRevert: (invocationId: number) => void;
}) {
  if (timeline.length === 0) return null;

  return (
    <div className="hl-section">
      <h4 className="hl-section-title">Timeline</h4>
      <div className="hl-panel hl-timeline">
        {timeline.map((event, i) => (
          <div key={i} className="hl-timeline-row" data-last={i === timeline.length - 1 ? "true" : undefined}>
            <Icon icon={TIMELINE_ICONS[event.kind]} size={14} className="hl-timeline-icon" data-reverted={event.reverted ? "true" : undefined} />
            <span className="hl-timeline-summary" data-reverted={event.reverted ? "true" : undefined}>
              <strong>{event.action_name.split(".").slice(1).join(".") || event.action_name}</strong>{" "}
              {TIMELINE_LABELS[event.kind]} by{" "}
              <span className="hl-mono">
                {event.actor_urn ? (principalsByUrn.get(event.actor_urn) ?? urnShortName(event.actor_urn)) : "—"}
              </span>
            </span>
            <span className="hl-timeline-reason">{event.reason}</span>
            {event.reverted && (
              <Tag minimal intent="none">
                reverted
              </Tag>
            )}
            {event.id !== null && event.id === nextRevertibleId && (
              <Button small minimal icon="undo" loading={reverting} onClick={() => onRevert(event.id as number)}>
                Undo
              </Button>
            )}
            <span className="hl-timeline-at">
              <FormattedValue rule={{ kind: "datetime", style: "relative" }} value={event.at} principalsByUrn={principalsByUrn} />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
