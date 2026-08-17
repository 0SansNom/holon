"""Action-domain Prometheus counters (P2d metrics)."""

from __future__ import annotations

from prometheus_client import Counter

ACTION_EVENTS = Counter(
    "holon_action_events_total",
    "Action Type lifecycle events",
    ["event", "action"],
)
