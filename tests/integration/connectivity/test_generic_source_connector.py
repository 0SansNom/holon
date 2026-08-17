"""Tests for Generic Source Connector."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, TENANT_ID, _unique_name, ontology_url, holon_url

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_sdk import HolonClient  # noqa: E402

REVIEWS_API = "http://reviews-api:8000/reviews.json"  # Connectivity's own network view, not the test runner's


client = HolonClient(identity_url=IDENTITY)
_request = client.request


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    try:
        return client.token_for(f"hl:{TENANT_ID}:global:user:jdoe")
    except TimeoutError as exc:
        pytest.fail(str(exc))


def test_registering_a_source_under_a_plugin_claimed_dataset_is_rejected(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": "customers", "base_url": REVIEWS_API},
    )
    assert status == 409, body
    assert "already claimed by active plugin" in body["detail"], body


def test_register_sync_and_catalog_a_brand_new_rest_source_with_zero_code(jdoe_token: str) -> None:
    name = _unique_name("no_code_reviews")

    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API},
    )
    assert status == 200, registration
    assert registration["name"] == name, registration
    # Never echoed back
    # the response shape must never carry a secret field at all.
    assert "auth_header_value" not in registration, registration

    status, sources = _request("GET", f"{CONNECTIVITY}/sources", token=jdoe_token)
    assert status == 200, sources
    assert any(s["name"] == name for s in sources), sources

    status, result = _request(
        "POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name}
    )
    assert status == 200, result
    assert result["row_count"] >= 1, result

    deadline = time.monotonic() + 30
    catalogued = None
    while time.monotonic() < deadline:
        status, datasets = _request("GET", holon_url("/catalog/datasets"), token=jdoe_token)
        assert status == 200, datasets
        catalogued = next((d for d in datasets if d["urn"] == result["dataset_urn"]), None)
        if catalogued is not None and catalogued["snapshot_id"] == result["snapshot_id"]:
            break
        time.sleep(1)
    assert catalogued is not None, "no-code source never converged in Knowledge's catalog"


def test_bad_record_path_surfaces_a_clear_400_not_a_500(jdoe_token: str) -> None:
    name = _unique_name("bad_record_path")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API, "record_path": "data.items"},
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name})
    assert status == 400, result
    assert "record_path" in result["detail"], result


def test_unreachable_url_surfaces_a_clear_400_not_a_500(jdoe_token: str) -> None:
    name = _unique_name("unreachable")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": "http://this-host-does-not-exist.invalid/x.json"},
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name})
    assert status == 400, result
    assert "could not reach the source" in result["detail"], result


def test_re_registering_the_same_name_updates_it_in_place(jdoe_token: str) -> None:
    name = _unique_name("idempotent_source")
    status, first = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API, "record_path": "wrong"},
    )
    assert status == 200, first

    status, second = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API},
    )
    assert status == 200, second
    assert second["record_path"] is None, second

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name})
    assert status == 200, result  # the corrected config is what actually applies


def test_editing_a_source_without_resending_the_secret_preserves_it(jdoe_token: str) -> None:
    name = _unique_name("secret_preserving")
    status, first = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API, "auth_header_name": "X-Api-Key", "auth_header_value": "shh"},
    )
    assert status == 200, first
    assert first["has_auth_header_value"] is True, first
    assert "auth_header_value" not in first, first  # the secret itself is never echoed back

    # Edit the URL only
    # secret it can't display
    # silently nulled out by the update.
    status, edited = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API + "?edited=true", "auth_header_name": "X-Api-Key"},
    )
    assert status == 200, edited
    assert edited["base_url"] == REVIEWS_API + "?edited=true", edited
    assert edited["has_auth_header_value"] is True, edited


def test_disabling_a_source_blocks_sync_with_a_clear_409_and_enabling_restores_it(jdoe_token: str) -> None:
    name = _unique_name("disable_enable")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token, body={"name": name, "base_url": REVIEWS_API}
    )
    assert status == 200, registration

    status, disabled = _request("POST", f"{CONNECTIVITY}/sources/{name}/disable", token=jdoe_token)
    assert status == 200, disabled
    assert disabled["status"] == "disabled", disabled

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name})
    assert status == 409, result
    assert "disabled" in result["detail"], result

    status, enabled = _request("POST", f"{CONNECTIVITY}/sources/{name}/enable", token=jdoe_token)
    assert status == 200, enabled
    assert enabled["status"] == "active", enabled

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name})
    assert status == 200, result


def test_deleting_a_source_removes_it_and_frees_the_name(jdoe_token: str) -> None:
    name = _unique_name("deletable")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token, body={"name": name, "base_url": REVIEWS_API}
    )
    assert status == 200, registration

    status, deleted = _request("DELETE", f"{CONNECTIVITY}/sources/{name}", token=jdoe_token)
    assert status == 200, deleted

    status, sources = _request("GET", f"{CONNECTIVITY}/sources", token=jdoe_token)
    assert status == 200, sources
    assert name not in [s["name"] for s in sources], sources

    # The name is genuinely free again, not just hidden
    # it must work exactly like a first-time registration.
    status, reregistered = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token, body={"name": name, "base_url": REVIEWS_API}
    )
    assert status == 200, reregistered


def test_disabling_or_deleting_an_unknown_source_is_404(jdoe_token: str) -> None:
    name = _unique_name("never_registered")
    status, body = _request("POST", f"{CONNECTIVITY}/sources/{name}/disable", token=jdoe_token)
    assert status == 404, body

    status, body = _request("DELETE", f"{CONNECTIVITY}/sources/{name}", token=jdoe_token)
    assert status == 404, body


# `reviews-api` serves static files (`python -m http.server`, no query-param
# routing)
# page looks, with two hand-linked fixture files rather than one dynamic
# endpoint: docker/reviews-api/paginated_reviews_page{1,2}.json.
PAGINATED_PAGE_1 = "http://reviews-api:8000/paginated_reviews_page1.json"
PAGINATED_LOOP = "http://reviews-api:8000/paginated_reviews_loop.json"


def test_paginated_source_follows_next_page_path_and_collects_every_row(jdoe_token: str) -> None:
    name = _unique_name("paginated")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": PAGINATED_PAGE_1, "record_path": "results", "next_page_path": "next"},
    )
    assert status == 200, registration
    assert registration["next_page_path"] == "next", registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name})
    assert status == 200, result
    # page1.json has 4 rows, page2.json (linked via "next") has the other
    assert result["row_count"] == 8, result


def test_source_without_next_page_path_only_reads_the_first_page(jdoe_token: str) -> None:
    """."""
    name = _unique_name("single_page_of_paginated_api")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": PAGINATED_PAGE_1, "record_path": "results"},
    )
    assert status == 200, registration
    assert registration["next_page_path"] is None, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name})
    assert status == 200, result
    assert result["row_count"] == 4, result


def test_a_source_that_never_terminates_its_next_page_chain_is_stopped_not_hung(jdoe_token: str) -> None:
    name = _unique_name("infinite_loop")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": PAGINATED_LOOP, "record_path": "results", "next_page_path": "next"},
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name})
    assert status == 400, result
    assert "stopped after" in result["detail"], result
    assert "pages" in result["detail"], result


# --- Reusable connections -------------------------------------------------


def test_a_connection_can_be_reused_by_two_different_sources(jdoe_token: str) -> None:
    connection_name = _unique_name("shared_auth")
    status, connection = _request(
        "POST", f"{CONNECTIVITY}/connections", token=jdoe_token,
        body={"name": connection_name, "auth_header_name": "X-Api-Key", "auth_header_value": "shh"},
    )
    assert status == 200, connection
    assert "auth_header_value" not in connection, connection  # never echoed back

    name_a = _unique_name("conn_source_a")
    name_b = _unique_name("conn_source_b")
    for name in (name_a, name_b):
        status, registration = _request(
            "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
            body={"name": name, "base_url": REVIEWS_API, "connection_name": connection_name},
        )
        assert status == 200, registration
        assert registration["connection_name"] == connection_name, registration

        status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name})
        assert status == 200, result
        assert result["row_count"] == 8, result


def test_connection_name_and_inline_auth_header_together_is_400(jdoe_token: str) -> None:
    connection_name = _unique_name("shared_auth")
    status, connection = _request(
        "POST", f"{CONNECTIVITY}/connections", token=jdoe_token,
        body={"name": connection_name, "auth_header_name": "X-Api-Key", "auth_header_value": "shh"},
    )
    assert status == 200, connection

    status, body = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={
            "name": _unique_name("conflicting_auth"),
            "base_url": REVIEWS_API,
            "connection_name": connection_name,
            "auth_header_name": "Authorization",
        },
    )
    assert status == 400, body


def test_registering_a_source_with_an_unknown_connection_is_400(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": _unique_name("bad_conn_source"), "base_url": REVIEWS_API, "connection_name": _unique_name("no_such_connection")},
    )
    assert status == 400, body


def test_deleting_a_connection_still_in_use_is_409_deleting_after_unlinking_works(jdoe_token: str) -> None:
    connection_name = _unique_name("deletable_connection")
    status, connection = _request(
        "POST", f"{CONNECTIVITY}/connections", token=jdoe_token,
        body={"name": connection_name, "auth_header_name": "X-Api-Key", "auth_header_value": "shh"},
    )
    assert status == 200, connection

    source_name = _unique_name("uses_deletable_connection")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": source_name, "base_url": REVIEWS_API, "connection_name": connection_name},
    )
    assert status == 200, registration

    status, body = _request("DELETE", f"{CONNECTIVITY}/connections/{connection_name}", token=jdoe_token)
    assert status == 409, body
    assert source_name in body["detail"], body

    status, body = _request("DELETE", f"{CONNECTIVITY}/sources/{source_name}", token=jdoe_token)
    assert status == 200, body

    status, body = _request("DELETE", f"{CONNECTIVITY}/connections/{connection_name}", token=jdoe_token)
    assert status == 200, body


def test_deleting_an_unknown_connection_is_404(jdoe_token: str) -> None:
    status, body = _request("DELETE", f"{CONNECTIVITY}/connections/{_unique_name('never_registered')}", token=jdoe_token)
    assert status == 404, body


# --- Scheduling ------------------------------------------------------------


def test_registering_a_source_with_a_non_positive_interval_is_400(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": _unique_name("bad_interval"), "base_url": REVIEWS_API, "schedule_interval_minutes": 0},
    )
    assert status == 400, body


def test_a_scheduled_source_syncs_itself_with_no_manual_trigger(jdoe_token: str) -> None:
    """The real test: register with a schedule and *never* call `/sync`."""
    name = _unique_name("auto_scheduled")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API, "schedule_interval_minutes": 1},
    )
    assert status == 200, registration
    assert registration["schedule_interval_minutes"] == 1, registration

    try:
        deadline = time.monotonic() + 100
        matched: dict | None = None
        while time.monotonic() < deadline:
            status, runs = _request("GET", f"{CONNECTIVITY}/syncs", token=jdoe_token)
            assert status == 200, runs
            matched = next((r for r in runs if r["dataset_urn"] == f"hl:acme:main:dataset:{name}"), None)
            if matched is not None:
                break
            time.sleep(5)
        assert matched is not None, f"scheduler never synced {name!r} within 100s"
        assert matched["row_count"] == 8, matched
    finally:
        # Left unregistered, a 1-minute-interval source would keep firing
        # forever in the background across every future test run in this
        # session
        _request("DELETE", f"{CONNECTIVITY}/sources/{name}", token=jdoe_token)


def test_a_source_without_a_schedule_is_never_touched_by_the_scheduler(jdoe_token: str) -> None:
    name = _unique_name("manual_only")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token, body={"name": name, "base_url": REVIEWS_API}
    )
    assert status == 200, registration
    assert registration["schedule_interval_minutes"] is None, registration

    time.sleep(65)  # past one full scheduler poll cycle
    status, runs = _request("GET", f"{CONNECTIVITY}/syncs", token=jdoe_token)
    assert status == 200, runs
    matched = next((r for r in runs if r["dataset_urn"] == f"hl:acme:main:dataset:{name}"), None)
    assert matched is None, matched


# --- Incremental sync (resume state) ---------------------------------------


def test_incremental_cursor_is_computed_and_persisted_after_a_sync(jdoe_token: str) -> None:
    """`reviews-api` is a static file server."""
    name = _unique_name("incremental_cursor")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={
            "name": name, "base_url": REVIEWS_API,
            "cursor_property": "id", "incremental_param": "since_id",
        },
    )
    assert status == 200, registration
    assert registration["last_cursor_value"] is None, registration  # nothing synced yet

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name})
    assert status == 200, result
    assert result["row_count"] == 8, result

    status, sources = _request("GET", f"{CONNECTIVITY}/sources", token=jdoe_token)
    assert status == 200, sources
    updated = next(s for s in sources if s["name"] == name)
    # reviews.json's highest "id" is 8
    # coerced numerically (not "8" < "10" as raw strings would wrongly say).
    assert updated["last_cursor_value"] == "8", updated

    # A second sync must not regress the cursor, even against a fixture
    # that doesn't actually filter by it and returns the same 8 rows again.
    status, result2 = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": name})
    assert status == 200, result2
    status, sources2 = _request("GET", f"{CONNECTIVITY}/sources", token=jdoe_token)
    assert status == 200, sources2
    updated2 = next(s for s in sources2 if s["name"] == name)
    assert updated2["last_cursor_value"] == "8", updated2


def test_registering_a_source_with_only_one_of_the_incremental_fields_is_accepted(jdoe_token: str) -> None:
    """Neither field requires the other at registration time."""
    name = _unique_name("cursor_only")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API, "cursor_property": "id"},
    )
    assert status == 200, registration
    assert registration["cursor_property"] == "id", registration
    assert registration["incremental_param"] is None, registration
