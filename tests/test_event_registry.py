"""Unit tests for the event payload schema registry.

Unlike the other files in this directory these are white-box unit tests of
`holon_common` — no running stack needed. They pin the contract that every
event_type emitted by a service is registered with a versioned payload
schema, and that drift (missing/extra fields, unknown version) fails fast.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))

from pydantic import ValidationError

from holon_common import EventEnvelope, registry
from holon_common.registry import UnknownEventTypeError, UnknownSchemaVersionError

# Every event_type currently emitted by a service (grep: event_type="...").
EMITTED_EVENT_TYPES = [
    "connectivity.sync.completed",
    "knowledge.action.requested",
    "knowledge.action.invoked",
    "knowledge.action.compensated",
    "knowledge.action.rejected",
    "knowledge.action.approval_expired",
]


def test_every_emitted_event_type_is_registered_v1():
    registered = dict.fromkeys(t for (t, _v) in registry.registered_event_types())
    for event_type in EMITTED_EVENT_TYPES:
        assert (event_type, 1) in registry.registered_event_types(), (
            f"{event_type} is emitted by a service but has no registered payload schema"
        )
        assert event_type in registered


def test_valid_sync_completed_payload_validates():
    model = registry.validate(
        "connectivity.sync.completed",
        1,
        {
            "connector_urn": "hl:acme:main:connector:erp",
            "dataset_name": "customers",
            "dataset_urn": "hl:acme:main:dataset:customers",
            "dataset_version_urn": "hl:acme:main:dataset-version:42",
            "iceberg_namespace": "holon",
            "iceberg_table": "customers",
            "snapshot_id": 42,
            "row_count": 100,
            "location": "s3://holon-warehouse/holon/customers",
        },
    )
    assert model.snapshot_id == 42


def test_unknown_event_type_rejected():
    with pytest.raises(UnknownEventTypeError):
        registry.validate("knowledge.dataset.version_created", 1, {})


def test_unknown_schema_version_rejected():
    with pytest.raises(UnknownSchemaVersionError):
        registry.validate("connectivity.sync.completed", 99, {})


def test_missing_required_field_rejected():
    with pytest.raises(ValidationError):
        registry.validate("knowledge.action.compensated", 1, {"action_name": "close", "instance_urn": "x"})


def test_extra_field_rejected():
    """extra="forbid": adding a field without a schema bump fails validation."""
    with pytest.raises(ValidationError):
        registry.validate(
            "knowledge.action.rejected",
            1,
            {"action_name": "close", "instance_urn": "x", "note": "n", "surprise": 1},
        )


def test_optional_fields_may_be_absent():
    model = registry.validate(
        "knowledge.action.invoked", 1, {"action_name": "close", "instance_urn": "x"}
    )
    assert model.approval_id is None


def test_envelope_and_registry_compose():
    """An envelope built the way services build it passes registry validation."""
    envelope = EventEnvelope(
        event_type="knowledge.action.approval_expired",
        tenant_id="acme",
        aggregate_type="ActionApproval",
        aggregate_id="hl:acme:main:action-approval:7",
        correlation_id="abc",
        partition_key="acme/hl:acme:main:action-approval:7",
        producer="knowledge-platform@0.1.0",
        actor={"type": "service_account", "urn": "hl:acme:global:service-account:knowledge"},
        payload={"action_name": "close_account", "instance_urn": "hl:acme:main:customer:1", "approval_id": 7},
    )
    model = registry.validate(envelope.event_type, envelope.schema_version, envelope.payload)
    assert model.approval_id == 7
    assert envelope.topic() == "knowledge"
