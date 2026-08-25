"""Tests for Pipeline."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, _request, _unique_name, ontology_url, holon_url


def _token_for(principal_urn: str) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        local_name = principal_urn.rsplit(":", 1)[-1]
        status, body = _request(
            "POST",
            f"{IDENTITY}/token",
            body={"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"},
        )
        if status == 200:
            return body["access_token"]
        time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


@pytest.fixture(scope="session")
def registered_function(msmith_token: str) -> dict:
    """Idempotent (`ON CONFLICT (name) DO UPDATE`), same idiom."""
    status, registration = _request(
        "POST", holon_url("/function-plugins"), token=msmith_token,
        body={"entry_point": "holon_test_plugins.flag_high_value_order_function:FlagHighValueOrderFunction"},
    )
    assert status == 200, registration
    assert registration["manifest"]["function_name"] == "flag_high_value_order", registration
    return registration


@pytest.fixture(scope="session")
def orders_synced(jdoe_token: str) -> dict:
    """Every pipeline step in this file reads the `orders` raw dataset."""
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "orders"})
    assert status == 200, result
    return result


def test_forward_reference_is_rejected_at_definition_time(jdoe_token: str) -> None:
    output_a, output_b = _unique_name("out-a"), _unique_name("out-b")
    status, body = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{_unique_name('bad-forward-ref')}", token=jdoe_token,
        body={"steps": [
            {"step_name": "s1", "input_dataset": output_b, "function_name": "flag_high_value_order", "output_dataset": output_a},
            {"step_name": "s2", "input_dataset": "orders", "function_name": "flag_high_value_order", "output_dataset": output_b},
        ]},
    )
    assert status == 400, body
    assert "reorder the steps" in body["detail"], body


def test_duplicate_step_name_is_rejected_at_definition_time(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{_unique_name('bad-dup')}", token=jdoe_token,
        body={"steps": [
            {"step_name": "dup", "input_dataset": "orders", "function_name": "flag_high_value_order", "output_dataset": _unique_name("o1")},
            {"step_name": "dup", "input_dataset": "orders", "function_name": "flag_high_value_order", "output_dataset": _unique_name("o2")},
        ]},
    )
    assert status == 400, body
    assert "duplicate step_name" in body["detail"], body


def test_empty_value_type_casts_rejected_at_definition_time(jdoe_token: str) -> None:
    status, body = _request(
        "POST",
        f"{CONNECTIVITY}/pipelines/{_unique_name('bad-casts')}",
        token=jdoe_token,
        body={
            "steps": [
                {
                    "step_name": "cast",
                    "input_dataset": "orders",
                    "function_name": "flag_high_value_order",
                    "output_dataset": _unique_name("out"),
                    "value_type_casts": {},
                }
            ]
        },
    )
    assert status == 400, body
    assert "value_type_casts" in body["detail"], body


def test_step_reading_and_writing_the_same_dataset_is_rejected(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{_unique_name('bad-self-ref')}", token=jdoe_token,
        body={"steps": [{"step_name": "s1", "input_dataset": "orders", "function_name": "flag_high_value_order", "output_dataset": "orders"}]},
    )
    assert status == 400, body
    assert "cannot read and write the same dataset" in body["detail"], body


def test_single_step_pipeline_is_catalogued_with_real_lineage(
    registered_function: dict, orders_synced: dict, jdoe_token: str
) -> None:
    output_dataset = _unique_name("orders_hv")
    pipeline_name = _unique_name("single-step-pipeline")

    status, definition = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token,
        body={"steps": [{"step_name": "flag-high-value", "input_dataset": "orders",
                          "function_name": "flag_high_value_order", "output_dataset": output_dataset}]},
    )
    assert status == 201, definition

    status, run = _request("POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}/run", token=jdoe_token)
    assert status == 200, run
    assert run["status"] == "succeeded", run
    assert len(run["step_results"]) == 1, run
    output_version_urn = run["step_results"][0]["dataset_version_urn"]
    assert run["step_results"][0]["dataset_urn"].endswith(f":{output_dataset}"), run

    deadline = time.monotonic() + 20
    datasets: list[dict] = []
    while time.monotonic() < deadline:
        status, datasets = _request("GET", holon_url("/catalog/datasets"), token=jdoe_token)
        assert status == 200, datasets
        if any(d["urn"].endswith(f":{output_dataset}") for d in datasets):
            break
        time.sleep(1)
    assert any(d["urn"].endswith(f":{output_dataset}") for d in datasets), datasets

    status, lineage = _request("GET", holon_url(f"/lineage/{output_version_urn}"), token=jdoe_token)
    assert status == 200, lineage
    derived_from_edges = [edge for edge in lineage if edge["relation"] == "derived_from"]
    assert len(derived_from_edges) == 1, lineage
    assert derived_from_edges[0]["target_urn"] == output_version_urn, lineage
    assert derived_from_edges[0]["source_urn"] == orders_synced["dataset_version_urn"], lineage


def test_chained_pipeline_produces_multi_hop_lineage(registered_function: dict, orders_synced: dict, jdoe_token: str) -> None:
    hop1_output = _unique_name("chain1")
    hop2_output = _unique_name("chain2")
    pipeline_name = _unique_name("chained-pipeline")

    status, definition = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token,
        body={"steps": [
            {"step_name": "hop1", "input_dataset": "orders", "function_name": "flag_high_value_order", "output_dataset": hop1_output},
            {"step_name": "hop2", "input_dataset": hop1_output, "function_name": "flag_high_value_order", "output_dataset": hop2_output},
        ]},
    )
    assert status == 201, definition

    status, run = _request("POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}/run", token=jdoe_token)
    assert status == 200, run
    assert run["status"] == "succeeded", run
    assert len(run["step_results"]) == 2, run
    hop1_version_urn = run["step_results"][0]["dataset_version_urn"]
    hop2_version_urn = run["step_results"][1]["dataset_version_urn"]

    deadline = time.monotonic() + 20
    lineage: list[dict] = []
    while time.monotonic() < deadline:
        status, lineage = _request("GET", holon_url(f"/lineage/{hop2_version_urn}"), token=jdoe_token)
        assert status == 200, lineage
        if any(edge["relation"] == "derived_from" for edge in lineage):
            break
        time.sleep(1)
    derived_from_edges = [edge for edge in lineage if edge["relation"] == "derived_from"]
    assert len(derived_from_edges) == 1, lineage
    assert derived_from_edges[0]["source_urn"] == hop1_version_urn, lineage
    assert derived_from_edges[0]["target_urn"] == hop2_version_urn, lineage


def test_run_against_a_dataset_that_was_never_synced_fails_cleanly(jdoe_token: str) -> None:
    pipeline_name = _unique_name("missing-input-pipeline")
    status, definition = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token,
        body={"steps": [{"step_name": "s1", "input_dataset": _unique_name("never-synced"),
                          "function_name": "flag_high_value_order", "output_dataset": _unique_name("out")}]},
    )
    assert status == 201, definition

    status, run = _request("POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}/run", token=jdoe_token)
    assert status == 400, run

    status, runs = _request("GET", f"{CONNECTIVITY}/pipelines/{pipeline_name}/runs", token=jdoe_token)
    assert status == 200, runs
    assert len(runs) == 1, runs
    assert runs[0]["status"] == "failed", runs
    assert runs[0]["step_results"] == [], runs


def test_run_against_an_unregistered_function_fails_cleanly(orders_synced: dict, jdoe_token: str) -> None:
    pipeline_name = _unique_name("bad-function-pipeline")
    status, definition = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token,
        body={"steps": [{"step_name": "s1", "input_dataset": "orders",
                          "function_name": _unique_name("nonexistent_function"), "output_dataset": _unique_name("out")}]},
    )
    assert status == 201, definition

    status, run = _request("POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}/run", token=jdoe_token)
    assert status == 400, run

    status, runs = _request("GET", f"{CONNECTIVITY}/pipelines/{pipeline_name}/runs", token=jdoe_token)
    assert status == 200, runs
    assert runs[0]["status"] == "failed", runs


def test_running_an_unknown_pipeline_is_404(jdoe_token: str) -> None:
    status, body = _request("POST", f"{CONNECTIVITY}/pipelines/{_unique_name('never-created')}/run", token=jdoe_token)
    assert status == 404, body


def test_delete_pipeline_removes_definition_and_runs(jdoe_token: str) -> None:
    pipeline_name = _unique_name("to-delete")
    status, definition = _request(
        "POST",
        f"{CONNECTIVITY}/pipelines/{pipeline_name}",
        token=jdoe_token,
        body={
            "steps": [
                {
                    "step_name": "s1",
                    "input_dataset": "orders",
                    "function_name": "flag_high_value_order",
                    "output_dataset": _unique_name("out"),
                }
            ]
        },
    )
    assert status == 201, definition

    # Force a failed run row so we prove cascade cleanup of pipeline_run.
    status, _ = _request("POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}/run", token=jdoe_token)
    assert status in (200, 400)

    status, deleted = _request("DELETE", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token)
    assert status == 200, deleted
    assert deleted["deleted"] == pipeline_name, deleted

    status, missing = _request("GET", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token)
    assert status == 404, missing

    status, runs = _request("GET", f"{CONNECTIVITY}/pipelines/{pipeline_name}/runs", token=jdoe_token)
    assert status == 200, runs
    assert runs == [], runs

    status, again = _request("DELETE", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token)
    assert status == 404, again


def test_pipeline_health_reflects_the_last_run(registered_function: dict, orders_synced: dict, jdoe_token: str) -> None:
    """P1a: `GET /pipelines/{name}` surfaces the same "health" Foundry's
    Data Health page shows — last status, the row count it produced,
    and freshness — without a separate call per pipeline.
    """
    pipeline_name = _unique_name("health-pipeline")
    status, definition = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token,
        body={"steps": [{"step_name": "s1", "input_dataset": "orders",
                          "function_name": "flag_high_value_order", "output_dataset": _unique_name("out")}]},
    )
    assert status == 201, definition
    assert definition["last_run"] is None, definition
    assert definition["last_success_at"] is None, definition
    assert definition["lag_seconds"] is None, definition
    assert definition["schedule_interval_minutes"] is None, definition

    status, run = _request("POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}/run", token=jdoe_token)
    assert status == 200 and run["status"] == "succeeded", run

    status, refetched = _request("GET", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token)
    assert status == 200, refetched
    assert refetched["last_run"]["status"] == "succeeded", refetched
    assert refetched["last_run"]["row_count"] == sum(s["row_count"] for s in run["step_results"]), refetched
    assert refetched["last_success_at"] is not None, refetched
    assert isinstance(refetched["lag_seconds"], int) and refetched["lag_seconds"] >= 0, refetched

    # A failed run afterwards must not clear last_success_at/lag — health
    # should show staleness growing against the last real success, not
    # reset to "no data" just because the latest attempt failed.
    status, bad_step = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token,
        body={"steps": [{"step_name": "s1", "input_dataset": "orders",
                          "function_name": _unique_name("nonexistent_function"), "output_dataset": _unique_name("out2")}]},
    )
    assert status == 201, bad_step
    status, failed_run = _request("POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}/run", token=jdoe_token)
    assert status == 400, failed_run

    status, after_failure = _request("GET", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token)
    assert status == 200, after_failure
    assert after_failure["last_run"]["status"] == "failed", after_failure
    assert after_failure["last_success_at"] == refetched["last_success_at"], after_failure


def test_setting_a_pipeline_schedule_validates_and_persists(
    registered_function: dict, orders_synced: dict, jdoe_token: str
) -> None:
    pipeline_name = _unique_name("scheduled-pipeline")
    status, _ = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token,
        body={"steps": [{"step_name": "s1", "input_dataset": "orders",
                          "function_name": "flag_high_value_order", "output_dataset": _unique_name("out")}]},
    )
    assert status == 201

    status, body = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}/schedule", token=jdoe_token,
        body={"schedule_interval_minutes": 0},
    )
    assert status == 400, body

    status, scheduled = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}/schedule", token=jdoe_token,
        body={"schedule_interval_minutes": 30},
    )
    assert status == 200 and scheduled["schedule_interval_minutes"] == 30, scheduled

    status, refetched = _request("GET", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token)
    assert status == 200 and refetched["schedule_interval_minutes"] == 30, refetched

    status, cleared = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}/schedule", token=jdoe_token,
        body={"schedule_interval_minutes": None},
    )
    assert status == 200 and cleared["schedule_interval_minutes"] is None, cleared

    status, missing = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{_unique_name('never-created')}/schedule", token=jdoe_token,
        body={"schedule_interval_minutes": 10},
    )
    assert status == 404, missing


def test_a_scheduled_pipeline_runs_itself_with_no_manual_trigger(
    registered_function: dict, orders_synced: dict, jdoe_token: str
) -> None:
    """Same real, not-mocked end-to-end wait as the scheduled-source and
    scheduled-plugin equivalents — same scheduler loop, same guarantee.
    """
    pipeline_name = _unique_name("auto-scheduled-pipeline")
    status, _ = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token,
        body={"steps": [{"step_name": "s1", "input_dataset": "orders",
                          "function_name": "flag_high_value_order", "output_dataset": _unique_name("out")}]},
    )
    assert status == 201

    status, scheduled = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{pipeline_name}/schedule", token=jdoe_token,
        body={"schedule_interval_minutes": 1},
    )
    assert status == 200 and scheduled["schedule_interval_minutes"] == 1, scheduled
    assert scheduled["last_run"] is None, scheduled  # never run manually — a brand new pipeline

    deadline = time.monotonic() + 100
    definition: dict = {}
    while time.monotonic() < deadline:
        status, definition = _request("GET", f"{CONNECTIVITY}/pipelines/{pipeline_name}", token=jdoe_token)
        assert status == 200, definition
        if definition["last_run"] is not None:
            break
        time.sleep(5)
    assert definition.get("last_run") is not None, f"scheduler never auto-ran {pipeline_name!r} within 100s"
    assert definition["last_run"]["status"] == "succeeded", definition
