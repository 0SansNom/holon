"""Kafka stream task lifecycle helpers for Connectivity."""
from __future__ import annotations

import asyncio
import logging

from holon_common import HolonError, Principal

from . import deps, stream_connector
from .deps import KAFKA_BOOTSTRAP, STREAM_INGEST_URN, WORKFLOW_ENGINE_URN

logger = logging.getLogger("connectivity.scheduler")


def _kafka_stream_task_key(tenant_id: str, name: str) -> tuple[str, str]:
    return (tenant_id, name)

def _spawn_kafka_stream_task(source: dict) -> None:
    """Start or restart a Kafka stream consumer task."""
    key = _kafka_stream_task_key(source["tenant_id"], source["name"])
    existing = deps.kafka_stream_tasks.get(key)
    if existing is not None and not existing.done():
        existing.cancel()
    connector_urn = build_urn(source["tenant_id"], "global", "connector", f"kafka-stream-{source['name']}")
    deps.kafka_stream_tasks[key] = asyncio.create_task(
        stream_connector.consume_stream_forever(
            source=source,
            kafka_bootstrap=KAFKA_BOOTSTRAP,
            iceberg_config=ICEBERG_CONFIG,
            connector_urn=connector_urn,
            pool=deps.pool,
            record_sync=functools.partial(
                _finalize_sync,
                actor=EventActor(type="service_account", urn=STREAM_INGEST_URN, on_behalf_of=None),
            ),
        )
    )

def _cancel_kafka_stream_task(tenant_id: str, name: str) -> None:
    key = _kafka_stream_task_key(tenant_id, name)
    task = deps.kafka_stream_tasks.pop(key, None)
    if task is not None and not task.done():
        task.cancel()

def _kafka_stream_not_found(name: str) -> HolonError:
    return HolonError.not_found("KafkaStreamNotFound", f"no Kafka stream registered as {name!r}", name=name)

def _plugin_not_found(name: str) -> HolonError:
    return HolonError.not_found("PluginNotFound", f"no plugin registered as {name!r}", name=name)

def _source_not_found(name: str) -> HolonError:
    return HolonError.not_found("SourceNotFound", f"no source registered as {name!r}", name=name)

def _require_workflow_engine(principal: Principal) -> None:
    """Verify caller is the Automation Workflow Engine service account."""
    if principal.type != "service_account" or principal.urn != WORKFLOW_ENGINE_URN:
        raise HolonError.forbidden(
            "AutomationOnlyEndpoint",
            "close-account is restricted to Automation's Workflow Engine — use the approval flow",
        )

