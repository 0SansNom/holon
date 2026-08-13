"""Pipeline / Transform DAG —
derived datasets *computed from other datasets*, not just one-hop
connector syncs. A `PipelineDefinition`'s steps apply a registered Phase A
**Function** to an existing Iceberg table (row -> row map, the third
Function call site alongside derived properties and Action side effects)
and write the result as a new dataset, reusing the *existing*
`_finalize_sync`/`connectivity.sync.completed` path so Catalog picks it
up automatically. Proves: a single-step pipeline's output is catalogued
with real `derived_from` lineage back to its input DatasetVersion; a
multi-hop chained pipeline (step 2 reads step 1's own output) produces a
real two-edge lineage chain; DAG-shape validation rejects a forward
reference and a duplicate step name at *definition* time; a missing input
dataset or an unregistered Function fails the *run* cleanly (a recorded
`failed` `pipeline_run`, not a crash) rather than at definition time,
matching this build's "validate what's checkable now, let execution be
the source of truth for the rest" precedent. No real LLM calls.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, _request


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
    """Idempotent (`ON CONFLICT (name) DO UPDATE`), same idiom
    `test_functions.py`'s own `registered` fixture already uses.
    """
    status, registration = _request(
        "POST", f"{KNOWLEDGE}/function-plugins", token=msmith_token,
        body={"entry_point": "app.plugins.flag_high_value_order_function:FlagHighValueOrderFunction"},
    )
    assert status == 200, registration
    assert registration["manifest"]["function_name"] == "flag_high_value_order", registration
    return registration


@pytest.fixture(scope="session")
def orders_synced(jdoe_token: str) -> dict:
    """Every pipeline step in this file reads the `orders` raw dataset —
    ensure it's actually been synced at least once in this run of the
    stack, the same precondition `test_relations.py` already sets up for
    itself.
    """
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "orders"})
    assert status == 200, result
    return result


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


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
        status, datasets = _request("GET", f"{KNOWLEDGE}/catalog/datasets", token=jdoe_token)
        assert status == 200, datasets
        if any(d["urn"].endswith(f":{output_dataset}") for d in datasets):
            break
        time.sleep(1)
    assert any(d["urn"].endswith(f":{output_dataset}") for d in datasets), datasets

    status, lineage = _request("GET", f"{KNOWLEDGE}/lineage/{output_version_urn}", token=jdoe_token)
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
        status, lineage = _request("GET", f"{KNOWLEDGE}/lineage/{hop2_version_urn}", token=jdoe_token)
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
