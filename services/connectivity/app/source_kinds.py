"""Registered no-code source kinds for sync and scheduling.

Each connector keeps its own registry module. Sync resolution and the
scheduler poll this table instead of hard-coding five else-if branches.
Plugins, Kafka streams, and pipelines stay on their own paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import asyncpg

from . import (
    generic_source_registry,
    object_source_registry,
    salesforce_source_registry,
    sftp_source_registry,
    sql_source_registry,
)

GetSource = Callable[[asyncpg.Pool, str, str], Awaitable[Optional[dict]]]
FetchForDataset = Callable[..., Awaitable[tuple[list[dict], Any]]]
ListAllScheduled = Callable[[asyncpg.Pool], Awaitable[list[dict]]]


@dataclass(frozen=True)
class SourceKind:
    """One registered source family (REST, SQL, object, SFTP, Salesforce)."""

    name: str
    get_source: GetSource
    fetch_for_dataset: FetchForDataset
    list_all_scheduled: ListAllScheduled
    connector_local_name: Callable[[str], str]
    uses_append: Callable[[dict], bool]


SOURCE_KINDS: tuple[SourceKind, ...] = (
    SourceKind(
        name="generic_rest",
        get_source=generic_source_registry.get_source,
        fetch_for_dataset=generic_source_registry.fetch_for_dataset,
        list_all_scheduled=generic_source_registry.list_all_scheduled_sources,
        connector_local_name=lambda dataset: f"generic-rest-{dataset}",
        uses_append=lambda row: bool(row.get("cursor_property")),
    ),
    SourceKind(
        name="sql",
        get_source=sql_source_registry.get_source,
        fetch_for_dataset=sql_source_registry.fetch_for_dataset,
        list_all_scheduled=sql_source_registry.list_all_scheduled_sources,
        connector_local_name=lambda dataset: f"sql-{dataset}",
        uses_append=lambda row: bool(row.get("cursor_property")),
    ),
    SourceKind(
        name="object",
        get_source=object_source_registry.get_source,
        fetch_for_dataset=object_source_registry.fetch_for_dataset,
        list_all_scheduled=object_source_registry.list_all_scheduled_sources,
        connector_local_name=lambda dataset: f"object-{dataset}",
        uses_append=lambda row: bool(row.get("incremental")),
    ),
    SourceKind(
        name="sftp",
        get_source=sftp_source_registry.get_source,
        fetch_for_dataset=sftp_source_registry.fetch_for_dataset,
        list_all_scheduled=sftp_source_registry.list_all_scheduled_sources,
        connector_local_name=lambda dataset: f"sftp-{dataset}",
        uses_append=lambda row: bool(row.get("incremental")),
    ),
    SourceKind(
        name="salesforce",
        get_source=salesforce_source_registry.get_source,
        fetch_for_dataset=salesforce_source_registry.fetch_for_dataset,
        list_all_scheduled=salesforce_source_registry.list_all_scheduled_sources,
        connector_local_name=lambda dataset: f"salesforce-{dataset}",
        uses_append=lambda row: bool(row.get("cursor_property")),
    ),
)


async def resolve_registered_source(
    pool: asyncpg.Pool, tenant_id: str, dataset_name: str
) -> Optional[tuple[SourceKind, dict]]:
    """Return the first kind that owns ``dataset_name`` for this tenant."""
    for kind in SOURCE_KINDS:
        row = await kind.get_source(pool, tenant_id, dataset_name)
        if row is not None:
            return kind, row
    return None
