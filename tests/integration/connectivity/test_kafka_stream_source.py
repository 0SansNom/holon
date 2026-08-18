"""Generic Kafka stream sources — register a topic (key field, target
dataset, batch window) declaratively, replacing the single hardcoded
inventory stream. `test_streaming_connector.py` covers that the
migrated inventory stream still works unchanged; this file covers the
registration mechanism itself against a fresh, ad-hoc topic.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest
from aiokafka import AIOKafkaProducer
from conftest import CONNECTIVITY, _request

KAFKA_BOOTSTRAP = "localhost:19092"


def _unique_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _publish(topic: str, messages: list[dict]) -> None:
    async def _send() -> None:
        producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await producer.start()
        try:
            for msg in messages:
                await producer.send_and_wait(topic, msg)
        finally:
            await producer.stop()

    asyncio.run(_send())


def _wait_for_sync(jdoe_token: str, dataset_name: str, *, min_row_count: int = 1) -> dict:
    deadline = time.monotonic() + 30
    matched: dict | None = None
    while time.monotonic() < deadline:
        status, runs = _request("GET", f"{CONNECTIVITY}/syncs", token=jdoe_token)
        assert status == 200, runs
        candidates = [r for r in runs if r["dataset_urn"].endswith(f":{dataset_name}") and r["row_count"] >= min_row_count]
        if candidates:
            matched = candidates[0]
            break
        time.sleep(1)
    assert matched is not None, f"stream never committed a sync for {dataset_name!r} within 30s"
    return matched


def test_a_registered_stream_consumes_its_own_topic_key_field_and_dataset(jdoe_token: str) -> None:
    topic = _unique_name("holon_test_topic")
    dataset_name = _unique_name("stream_widgets")
    name = _unique_name("widget-stream")

    _publish(topic, [
        {"widget_id": "W1", "color": "red", "count": 3},
        {"widget_id": "W2", "color": "blue", "count": 7},
    ])

    status, registration = _request(
        "POST", f"{CONNECTIVITY}/kafka-streams", token=jdoe_token,
        body={"name": name, "topic": topic, "key_field": "widget_id", "dataset_name": dataset_name, "batch_interval_seconds": 2},
    )
    assert status == 201, registration
    assert registration["topic"] == topic, registration
    assert registration["key_field"] == "widget_id", registration
    assert registration["status"] == "active", registration

    try:
        matched = _wait_for_sync(jdoe_token, dataset_name, min_row_count=2)
        assert matched["row_count"] == 2, matched
    finally:
        _request("DELETE", f"{CONNECTIVITY}/kafka-streams/{name}", token=jdoe_token)


def test_a_registered_stream_keeps_only_the_latest_message_per_key(jdoe_token: str) -> None:
    """`key_field` isn't just labeling — it's what "latest reading" is
    computed per. Two updates to the same widget must collapse to one
    row, not two.
    """
    topic = _unique_name("holon_test_topic")
    dataset_name = _unique_name("stream_widgets_dedup")
    name = _unique_name("widget-dedup-stream")

    _publish(topic, [
        {"widget_id": "W1", "color": "red", "count": 3},
        {"widget_id": "W1", "color": "green", "count": 9},
    ])

    status, registration = _request(
        "POST", f"{CONNECTIVITY}/kafka-streams", token=jdoe_token,
        body={"name": name, "topic": topic, "key_field": "widget_id", "dataset_name": dataset_name, "batch_interval_seconds": 2},
    )
    assert status == 201, registration

    try:
        matched = _wait_for_sync(jdoe_token, dataset_name, min_row_count=1)
        assert matched["row_count"] == 1, matched
    finally:
        _request("DELETE", f"{CONNECTIVITY}/kafka-streams/{name}", token=jdoe_token)


def test_two_streams_cannot_claim_the_same_dataset(jdoe_token: str) -> None:
    dataset_name = _unique_name("stream_conflict_target")
    first_name = _unique_name("stream-a")
    second_name = _unique_name("stream-b")

    status, first = _request(
        "POST", f"{CONNECTIVITY}/kafka-streams", token=jdoe_token,
        body={"name": first_name, "topic": _unique_name("topic"), "key_field": "id", "dataset_name": dataset_name},
    )
    assert status == 201, first

    try:
        status, body = _request(
            "POST", f"{CONNECTIVITY}/kafka-streams", token=jdoe_token,
            body={"name": second_name, "topic": _unique_name("topic"), "key_field": "id", "dataset_name": dataset_name},
        )
        assert status == 409, body
        assert first_name in body["detail"], body
    finally:
        _request("DELETE", f"{CONNECTIVITY}/kafka-streams/{first_name}", token=jdoe_token)


def test_disable_and_enable_flip_status_and_a_re_enabled_stream_consumes_again(jdoe_token: str) -> None:
    topic = _unique_name("holon_test_topic")
    dataset_name = _unique_name("stream_disable_target")
    name = _unique_name("widget-disable-stream")

    status, registration = _request(
        "POST", f"{CONNECTIVITY}/kafka-streams", token=jdoe_token,
        body={"name": name, "topic": topic, "key_field": "widget_id", "dataset_name": dataset_name, "batch_interval_seconds": 2},
    )
    assert status == 201, registration

    status, disabled = _request("POST", f"{CONNECTIVITY}/kafka-streams/{name}/disable", token=jdoe_token)
    assert status == 200 and disabled["status"] == "disabled", disabled

    status, enabled = _request("POST", f"{CONNECTIVITY}/kafka-streams/{name}/enable", token=jdoe_token)
    assert status == 200 and enabled["status"] == "active", enabled

    _publish(topic, [{"widget_id": "W9", "color": "black", "count": 1}])
    try:
        matched = _wait_for_sync(jdoe_token, dataset_name, min_row_count=1)
        assert matched["row_count"] == 1, matched
    finally:
        _request("DELETE", f"{CONNECTIVITY}/kafka-streams/{name}", token=jdoe_token)


def test_kafka_streams_list_and_delete(jdoe_token: str) -> None:
    name = _unique_name("widget-list-stream")
    status, _ = _request(
        "POST", f"{CONNECTIVITY}/kafka-streams", token=jdoe_token,
        body={"name": name, "topic": _unique_name("topic"), "key_field": "id", "dataset_name": _unique_name("stream_list_target")},
    )
    assert status == 201

    status, streams = _request("GET", f"{CONNECTIVITY}/kafka-streams", token=jdoe_token)
    assert status == 200, streams
    assert any(s["name"] == name for s in streams), streams

    status, deleted = _request("DELETE", f"{CONNECTIVITY}/kafka-streams/{name}", token=jdoe_token)
    assert status == 200 and deleted == {"deleted": name}, deleted

    status, missing = _request("GET", f"{CONNECTIVITY}/kafka-streams/{name}", token=jdoe_token)
    assert status == 404, missing

    status, second_delete = _request("DELETE", f"{CONNECTIVITY}/kafka-streams/{name}", token=jdoe_token)
    assert status == 404, second_delete
