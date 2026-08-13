"""The second half of the no-code connector: turning an already-synced
Dataset (see `test_generic_source_connector.py`) into a real, browsable
ObjectType — `POST /object-types`, then the generic `GET /objects/{type}`
read path (`routers/objects.py`'s last two routes), proving the dynamic
fallback actually generalizes rather than only working for the six
historical demo ObjectTypes it was carved out of.

Ordering matters and is deliberately exercised here: `/sync` only
catalogues/materializes against whatever ObjectType mapping exists *at
that moment* (`catalog.py`'s consumer, event-driven) — a source is always
synced *before* its ObjectType can be created (columns must exist to be
previewed/mapped), so the first sync's materialization is skipped
("catalogued, not yet an ObjectType", logged, not an error). A second
sync after creation is what actually catches it up — proven below via
the same `degraded` flag / search-facet checks the rest of this suite
already uses for materialization convergence.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, TENANT_ID, _unique_name, as_items

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))

from holon_sdk import HolonClient  # noqa: E402

REVIEWS_API = "http://reviews-api:8000/reviews.json"


client = HolonClient(identity_url=IDENTITY)
_request = client.request


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    try:
        return client.token_for(f"hl:{TENANT_ID}:global:user:jdoe")
    except TimeoutError as exc:
        pytest.fail(str(exc))


@pytest.fixture(scope="session")
def msmith_token() -> str:
    try:
        return client.token_for(f"hl:{TENANT_ID}:global:user:msmith")
    except TimeoutError as exc:
        pytest.fail(str(exc))


@pytest.fixture(scope="session")
def kenji_token() -> str:
    try:
        return client.token_for(f"hl:{TENANT_ID}:global:user:kenji")
    except TimeoutError as exc:
        pytest.fail(str(exc))


def _connect_and_sync_source(token: str, name: str) -> dict:
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=token, body={"name": name, "base_url": REVIEWS_API}
    )
    assert status == 200, registration
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=token, body={"dataset": name})
    assert status == 200, result
    return result


def test_creating_an_object_type_requires_admin_governance_tier(jdoe_token: str) -> None:
    dataset_name = _unique_name("gov_check_source")
    sync_result = _connect_and_sync_source(jdoe_token, dataset_name)

    status, body = _request(
        "POST", f"{KNOWLEDGE}/object-types", token=jdoe_token,
        body={
            "name": _unique_name("GovCheckType"),
            "source_dataset_urn": sync_result["dataset_urn"],
            "property_mapping": {"id": "id"},
        },
    )
    assert status == 403, body


def test_creating_an_object_type_under_an_existing_name_is_rejected(msmith_token: str) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/object-types", token=msmith_token,
        body={"name": "Customer", "source_dataset_urn": "hl:acme:main:dataset:whatever", "property_mapping": {"id": "id"}},
    )
    assert status == 409, body


def test_preview_returns_real_column_names_and_sample_values(jdoe_token: str) -> None:
    dataset_name = _unique_name("preview_check_source")
    _connect_and_sync_source(jdoe_token, dataset_name)

    status, preview = _request("GET", f"{KNOWLEDGE}/catalog/datasets/{dataset_name}/preview", token=jdoe_token)
    assert status == 200, preview
    columns = {c["name"] for c in preview["columns"]}
    assert {"id", "order_id", "rating", "comment"} <= columns, preview


def test_preview_of_a_never_synced_dataset_is_404(jdoe_token: str) -> None:
    status, body = _request(
        "GET", f"{KNOWLEDGE}/catalog/datasets/{_unique_name('never_synced')}/preview", token=jdoe_token
    )
    assert status == 404, body


def test_full_self_serve_loop_source_to_browsable_and_searchable_object(jdoe_token: str, msmith_token: str) -> None:
    dataset_name = _unique_name("full_loop_source")
    sync_result = _connect_and_sync_source(jdoe_token, dataset_name)
    type_name = _unique_name("FullLoopReview")

    status, created = _request(
        "POST", f"{KNOWLEDGE}/object-types", token=msmith_token,
        body={
            "name": type_name,
            "source_dataset_urn": sync_result["dataset_urn"],
            "property_mapping": {"id": "id", "orderId": "order_id", "rating": "rating", "comment": "comment"},
            "description": "Created by test_full_self_serve_loop.",
        },
    )
    assert status == 201, created
    assert created["source_dataset_urn"] == sync_result["dataset_urn"], created

    # Shows up in the same generic listing the Objects nav page reads.
    status, listing = _request("GET", f"{KNOWLEDGE}/ontology", token=jdoe_token)
    assert status == 200, listing
    assert type_name in [t["name"] for t in listing], listing

    # Immediately readable — generic dispatch, no code, no restart.
    status, body = _request("GET", f"{KNOWLEDGE}/objects/{type_name}", token=jdoe_token)
    assert status == 200, body
    rows = as_items(body)
    assert len(rows) == 8, rows
    assert all(row["degraded"] is True for row in rows), rows  # first sync predates the type — live federated read only

    status, one = _request("GET", f"{KNOWLEDGE}/objects/{type_name}/1", token=jdoe_token)
    assert status == 200, one
    assert one["id"] == 1, one

    # Re-sync (now that the type exists) is what actually materializes
    # it into the fast-path store and indexes it into search — the
    # ordering gap this module's docstring explains.
    status, resync_result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": dataset_name})
    assert status == 200, resync_result

    deadline = time.monotonic() + 30
    materialized = None
    while time.monotonic() < deadline:
        status, materialized = _request("GET", f"{KNOWLEDGE}/objects/{type_name}/1", token=jdoe_token)
        assert status == 200, materialized
        if materialized["degraded"] is False:
            break
        time.sleep(1)
    assert materialized["degraded"] is False, materialized
    assert materialized["materializedAt"] is not None, materialized

    deadline = time.monotonic() + 30
    search_result = None
    while time.monotonic() < deadline:
        status, search_result = _request("GET", f"{KNOWLEDGE}/search?q=milling+machine", token=jdoe_token)
        assert status == 200, search_result
        if type_name in search_result.get("facets", {}):
            break
        time.sleep(1)
    assert type_name in search_result["facets"], search_result


def test_unknown_object_type_is_404_not_500(jdoe_token: str) -> None:
    status, body = _request("GET", f"{KNOWLEDGE}/objects/{_unique_name('DoesNotExist')}", token=jdoe_token)
    assert status == 404, body

    status, body = _request("GET", f"{KNOWLEDGE}/objects/{_unique_name('DoesNotExist')}/1", token=jdoe_token)
    assert status == 404, body


def test_creating_with_an_unknown_classification_value_is_400(jdoe_token: str, msmith_token: str) -> None:
    dataset_name = _unique_name("bad_classif_source")
    sync_result = _connect_and_sync_source(jdoe_token, dataset_name)

    status, body = _request(
        "POST", f"{KNOWLEDGE}/object-types", token=msmith_token,
        body={
            "name": _unique_name("BadClassifType"),
            "source_dataset_urn": sync_result["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment"},
            "column_classification": {"comment": "top-secret"},
        },
    )
    assert status == 400, body
    assert "top-secret" in body["detail"], body


def test_creating_with_an_empty_property_mapping_is_400(jdoe_token: str, msmith_token: str) -> None:
    dataset_name = _unique_name("empty_mapping_source")
    sync_result = _connect_and_sync_source(jdoe_token, dataset_name)

    status, body = _request(
        "POST", f"{KNOWLEDGE}/object-types", token=msmith_token,
        body={"name": _unique_name("EmptyMappingType"), "source_dataset_urn": sync_result["dataset_urn"], "property_mapping": {}},
    )
    assert status == 400, body


def test_declared_confidential_column_is_actually_masked_for_an_abac_restricted_principal(
    jdoe_token: str, msmith_token: str, kenji_token: str
) -> None:
    """The point of this feature, proven end to end: a self-serve admin's
    classification choice has real read-time effect — not just metadata
    displayed somewhere — the same field-level masking the six seeded
    types get from their hand-written `*_COLUMN_CLASSIFICATION` constants.
    """
    dataset_name = _unique_name("masking_check_source")
    sync_result = _connect_and_sync_source(jdoe_token, dataset_name)
    type_name = _unique_name("MaskingCheckReview")

    status, created = _request(
        "POST", f"{KNOWLEDGE}/object-types", token=msmith_token,
        body={
            "name": type_name,
            "source_dataset_urn": sync_result["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment", "rating": "rating"},
            "column_classification": {"comment": "confidential", "id": "public"},
        },
    )
    assert status == 201, created
    assert created["classification"] == "confidential", created  # most_restrictive(public, confidential, internal)
    assert created["column_classification"] == {"comment": "confidential", "id": "public"}, created

    # The type's own re-sync (same one the UI auto-triggers) is what
    # actually materializes it with this classification in effect.
    status, resynced = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": dataset_name})
    assert status == 200, resynced

    deadline = time.monotonic() + 30
    jdoe_row: dict = {}
    while time.monotonic() < deadline:
        status, jdoe_row = _request("GET", f"{KNOWLEDGE}/objects/{type_name}/1", token=jdoe_token)
        assert status == 200, jdoe_row
        if jdoe_row.get("degraded") is False:
            break
        time.sleep(1)
    assert jdoe_row["degraded"] is False, jdoe_row
    assert jdoe_row["comment"] is not None, jdoe_row  # France, ABAC-unrestricted: sees it for real

    status, kenji_row = _request("GET", f"{KNOWLEDGE}/objects/{type_name}/1", token=kenji_token)
    assert status == 200, kenji_row
    assert kenji_row["comment"] is None, kenji_row
    assert "comment" in kenji_row.get("_maskedFields", []), kenji_row
    assert kenji_row["rating"] is not None, kenji_row  # not classified -> defaults internal, not masked from kenji
