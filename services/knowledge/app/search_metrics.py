"""Search index health gauges: what the serving store has that search cannot find."""

from __future__ import annotations

from prometheus_client import Gauge

SEARCH_ROWS_SKIPPED_INVALID = Gauge(
    "holon_search_rows_skipped_invalid",
    "Rows of the latest snapshot left out of the search index (Value Type validation failed)",
    ["tenant", "object_type"],
)
SEARCH_REINDEX_FAILED_OBJECT_TYPES = Gauge(
    "holon_search_reindex_failed_object_types",
    "Object types the startup search reindex has not managed to rewrite yet",
)
SEARCH_DOCUMENTS_OUTSIDE_POLICY = Gauge(
    "holon_search_documents_outside_policy",
    "Search documents not on the current policy_version (invisible to queries); -1 when unknown",
)
