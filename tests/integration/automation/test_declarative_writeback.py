"""Tests for Declarative Writeback."""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request

import asyncpg
import pytest
from conftest import CONNECTIVITY, KNOWLEDGE, _request, _unique_name, ontology_url, holon_url

REVIEWS_WITH_TAGS_API = "http://reviews-api:8000/reviews_with_tags.json"

SOURCE_ERP_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/source_erp"


@pytest.fixture(scope="module")
def source_table():
    """A dedicated writeback target table, created/dropped around this."""

    async def _create():
        conn = await asyncpg.connect(SOURCE_ERP_URL)
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS writeback_test_target (id INTEGER PRIMARY KEY, status TEXT NOT NULL, notes TEXT)"
            )
            await conn.execute("DELETE FROM writeback_test_target")
            await conn.execute(
                "INSERT INTO writeback_test_target (id, status, notes) VALUES (1, 'new', NULL), (2, 'new', NULL)"
            )
        finally:
            await conn.close()

    async def _drop():
        conn = await asyncpg.connect(SOURCE_ERP_URL)
        try:
            await conn.execute("DROP TABLE IF EXISTS writeback_test_target")
        finally:
            await conn.close()

    asyncio.run(_create())
    yield
    asyncio.run(_drop())


def _read_source_row(row_id: int) -> dict:
    async def _get():
        conn = await asyncpg.connect(SOURCE_ERP_URL)
        try:
            row = await conn.fetchrow("SELECT * FROM writeback_test_target WHERE id = $1", row_id)
            return dict(row) if row else None
        finally:
            await conn.close()

    return asyncio.run(_get())


def _register_sync_and_create_object_type(msmith_token: str, jdoe_token: str) -> str:
    source_name = _unique_name("writeback_reviews")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": source_name, "base_url": REVIEWS_WITH_TAGS_API},
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result

    object_type_name = _unique_name("WritebackReview")
    status, object_type = _request(
        "POST", holon_url("/object-types"), token=msmith_token,
        body={
            "name": object_type_name,
            "source_dataset_urn": result["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment"},
            "description": "pytest-created",
        },
    )
    assert status == 201, object_type
    return object_type_name


def _register_write_target(jdoe_token: str, *, table_name: str = "writeback_test_target") -> str:
    dataset_name = _unique_name("writeback_target")
    # `allowed_properties` maps the ontology-facing property name
    # ("processingStatus") to the real source column ("status")
    # need not match, and deliberately don't here, to keep the ontology
    # property clear of `_RESERVED_RESPONSE_KEYS` while allowing the
    # source column name to differ.
    status, target = _request(
        "POST", f"{CONNECTIVITY}/write-targets", token=jdoe_token,
        body={
            "dataset_name": dataset_name, "table_name": table_name, "id_column": "id",
            "allowed_properties": {"processingStatus": "status", "notes": "notes"},
        },
    )
    assert status == 201, target
    return dataset_name


def test_direct_write_calls_are_restricted_to_the_workflow_engine(jdoe_token: str) -> None:
    """`POST /source/{dataset_name}/{instance_id}/write` is an internal."""
    status, result = _request(
        "POST", f"{CONNECTIVITY}/source/{_unique_name('irrelevant')}/1/write", token=jdoe_token,
        body={"edits": {"processingStatus": "x"}},
    )
    assert status == 403, result


def test_write_targets_list_includes_registered_targets_and_delete_removes_them(jdoe_token: str) -> None:
    """`GET`/`DELETE /write-targets` — the Data Sources UI's writeback
    visibility (P0b): the Action-based writeback path already existed,
    this is just surfacing what's registered instead of API-only.
    """
    dataset_name = _register_write_target(jdoe_token)

    status, targets = _request("GET", f"{CONNECTIVITY}/write-targets", token=jdoe_token)
    assert status == 200, targets
    registered = next((t for t in targets if t["dataset_name"] == dataset_name), None)
    assert registered is not None, targets
    assert registered["table_name"] == "writeback_test_target", registered
    assert registered["allowed_properties"] == {"processingStatus": "status", "notes": "notes"}, registered

    status, deleted = _request("DELETE", f"{CONNECTIVITY}/write-targets/{dataset_name}", token=jdoe_token)
    assert status == 200 and deleted == {"deleted": dataset_name}, deleted

    status, gone = _request("GET", f"{CONNECTIVITY}/write-targets/{dataset_name}", token=jdoe_token)
    assert status == 404, gone

    status, second_delete = _request("DELETE", f"{CONNECTIVITY}/write-targets/{dataset_name}", token=jdoe_token)
    assert status == 404, second_delete


def test_declarative_action_type_requires_high_risk_for_a_writeback_dataset(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    dataset_name = _register_write_target(jdoe_token)
    status, result = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": f"{object_type_name}.badLowRiskWriteback", "target_object_type": object_type_name,
            "required_permission": "write", "risk_level": "low", "description": "should be rejected",
            "edits": [{"property": "processingStatus", "source": "literal", "value": "x"}],
            "writeback_dataset": dataset_name,
        },
    )
    assert status == 400, result
    assert "risk_level='high'" in result["detail"], result


def test_an_edit_property_colliding_with_a_reserved_response_key_is_rejected(msmith_token: str, jdoe_token: str) -> None:
    """An edit literally named "status" (or any other control field the."""
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    status, result = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": f"{object_type_name}.badReservedEdit", "target_object_type": object_type_name,
            "required_permission": "write", "risk_level": "low", "description": "should be rejected",
            "edits": [{"property": "status", "source": "literal", "value": "x"}],
        },
    )
    assert status == 400, result
    assert "reserved response field" in result["detail"], result


def test_declarative_writeback_completes_the_saga_end_to_end(msmith_token: str, jdoe_token: str, source_table) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    dataset_name = _register_write_target(jdoe_token)
    action_name = f"{object_type_name}.markProcessed"
    status, action_type = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": action_name, "target_object_type": object_type_name, "required_permission": "write",
            "risk_level": "high", "description": "marks the source row processed, writes back",
            "edits": [{"property": "processingStatus", "source": "literal", "value": "processed"}],
            "writeback_dataset": dataset_name,
        },
    )
    assert status == 201, action_type
    assert action_type["writeback_dataset"] == dataset_name, action_type

    status, requested = _request(
        "POST", ontology_url(f"/objects/{object_type_name}/1/actions/{action_name}"), token=jdoe_token,
        body={"reason": "saga happy path"},
    )
    assert status == 200, requested
    assert requested["status"] == "pending_approval", requested
    approval_id = requested["approvalId"]

    status, approved = _request("POST", holon_url(f"/approvals/{approval_id}/approve"), token=msmith_token, body={})
    assert status == 200, approved
    # `sagaStatus` must say "processing" here, not just for the one
    # hardcoded closeAccount action
    # yet at this point for a declarative writeback action either.
    assert approved["sagaStatus"] == "processing", approved
    assert approved["status"] == "approved", approved  # not clobbered by the edit's own value

    deadline = time.monotonic() + 30
    instance, source_row = {}, None
    while time.monotonic() < deadline:
        status, instance = _request("GET", ontology_url(f"/objects/{object_type_name}/1"), token=jdoe_token)
        assert status == 200, instance
        source_row = _read_source_row(1)
        if instance.get("processingStatus") == "processed" and source_row is not None and source_row["status"] == "processed":
            break
        time.sleep(1)
    assert instance.get("processingStatus") == "processed", instance
    assert source_row is not None and source_row["status"] == "processed", source_row


def test_declarative_writeback_failure_compensates_both_sides(msmith_token: str, jdoe_token: str, source_table) -> None:
    """No sentinel available for the generic path (unlike closeAccount's."""
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    never_registered_dataset = _unique_name("never_registered_write_target")
    action_name = f"{object_type_name}.failingWriteback"
    status, action_type = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": action_name, "target_object_type": object_type_name, "required_permission": "write",
            "risk_level": "high", "description": "writeback target intentionally never registered",
            "edits": [{"property": "processingStatus", "source": "literal", "value": "processed"}],
            "writeback_dataset": never_registered_dataset,
        },
    )
    assert status == 201, action_type

    status, requested = _request(
        "POST", ontology_url(f"/objects/{object_type_name}/2/actions/{action_name}"), token=jdoe_token,
        body={"reason": "should fail and compensate"},
    )
    assert status == 200, requested
    approval_id = requested["approvalId"]

    status, approved = _request("POST", holon_url(f"/approvals/{approval_id}/approve"), token=msmith_token, body={})
    assert status == 200, approved
    assert approved["sagaStatus"] == "processing", approved

    deadline = time.monotonic() + 30
    approval, instance = {}, {}
    while time.monotonic() < deadline:
        status, approval = _request("GET", holon_url(f"/approvals/{approval_id}"), token=msmith_token)
        assert status == 200
        if approval["status"] == "failed":
            break
        time.sleep(1)
    assert approval["status"] == "failed", approval

    # Knowledge's own overlay must have been reverted, not left showing
    # an edit that was actually compensated.
    status, instance = _request("GET", ontology_url(f"/objects/{object_type_name}/2"), token=jdoe_token)
    assert status == 200, instance
    assert "processingStatus" not in instance, instance

    # The source system must never actually have been changed either.
    source_row = _read_source_row(2)
    assert source_row is not None and source_row["status"] == "new", source_row
