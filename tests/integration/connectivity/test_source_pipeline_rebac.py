"""Integration tests for Connectivity source/pipeline ReBAC."""

from __future__ import annotations

import subprocess
import textwrap
import time
from pathlib import Path

from conftest import CONNECTIVITY, TENANT_ID, WORKSPACE_ID, _request, _unique_name

REVIEWS_API = "http://reviews-api:8000/reviews.json"
REPO_ROOT = Path(__file__).resolve().parents[3]
# PermissionClient decision cache TTL is 5s — wait past it after SpiceDB mutations.
_AUTHZ_CACHE_PAD_SECONDS = 6.0


def _spicedb_mutate(*, resource_type: str, resource_urn: str, operation: str) -> None:
    """Touch/delete parent_workspace from inside Connectivity (has the compose SpiceDB key)."""
    script = textwrap.dedent(
        f"""
        import asyncio, os
        from holon_common.authz import PermissionClient
        from holon_common import build_urn

        async def main():
            client = PermissionClient(
                os.environ["HOLON_SPICEDB_URL"],
                os.environ["HOLON_SPICEDB_PRESHARED_KEY"],
                os.environ["HOLON_OPA_URL"],
            )
            try:
                kwargs = dict(
                    resource_type={resource_type!r},
                    resource_urn={resource_urn!r},
                    relation="parent_workspace",
                    subject_type="workspace",
                    subject_urn=build_urn({TENANT_ID!r}, "global", "workspace", {WORKSPACE_ID!r}),
                )
                if {operation!r} == "delete":
                    await client.delete_relationship(**kwargs)
                else:
                    await client.write_relationship(**kwargs)
            finally:
                await client.aclose()

        asyncio.run(main())
        """
    )
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "connectivity", "python", "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"spicedb mutate failed: {result.stderr or result.stdout}"


def _source_urn(name: str) -> str:
    return f"hl:{TENANT_ID}:{WORKSPACE_ID}:source:{name}"


def _pipeline_urn(name: str) -> str:
    return f"hl:{TENANT_ID}:{WORKSPACE_ID}:pipeline:{name}"


def test_create_source_seeds_rebac_and_workspace_editor_can_delete(jdoe_token: str) -> None:
    name = _unique_name("rebac_src")
    status, registration = _request(
        "POST",
        f"{CONNECTIVITY}/sources",
        token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API},
    )
    assert status == 200, registration

    status, deleted = _request("DELETE", f"{CONNECTIVITY}/sources/{name}", token=jdoe_token)
    assert status == 200, deleted


def test_source_delete_denied_after_parent_workspace_unlinked(jdoe_token: str) -> None:
    name = _unique_name("rebac_unlink")
    status, registration = _request(
        "POST",
        f"{CONNECTIVITY}/sources",
        token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API},
    )
    assert status == 200, registration

    _spicedb_mutate(resource_type="source", resource_urn=_source_urn(name), operation="delete")
    time.sleep(_AUTHZ_CACHE_PAD_SECONDS)

    status, body = _request("DELETE", f"{CONNECTIVITY}/sources/{name}", token=jdoe_token)
    assert status == 403, body

    _spicedb_mutate(resource_type="source", resource_urn=_source_urn(name), operation="touch")
    time.sleep(_AUTHZ_CACHE_PAD_SECONDS)
    status, deleted = _request("DELETE", f"{CONNECTIVITY}/sources/{name}", token=jdoe_token)
    assert status == 200, deleted


def test_create_pipeline_seeds_rebac_and_unlink_denies_delete(jdoe_token: str) -> None:
    name = _unique_name("rebac_pipe")
    status, created = _request(
        "POST",
        f"{CONNECTIVITY}/pipelines/{name}",
        token=jdoe_token,
        body={
            "steps": [
                {
                    "step_name": "s1",
                    "input_dataset": "orders",
                    "function_name": "flag_high_value_order",
                    "output_dataset": f"{name}_out",
                }
            ]
        },
    )
    assert status in (200, 201), created

    status, got = _request("GET", f"{CONNECTIVITY}/pipelines/{name}", token=jdoe_token)
    assert status == 200, got

    _spicedb_mutate(resource_type="pipeline", resource_urn=_pipeline_urn(name), operation="delete")
    time.sleep(_AUTHZ_CACHE_PAD_SECONDS)

    status, body = _request("DELETE", f"{CONNECTIVITY}/pipelines/{name}", token=jdoe_token)
    assert status == 403, body

    _spicedb_mutate(resource_type="pipeline", resource_urn=_pipeline_urn(name), operation="touch")
    time.sleep(_AUTHZ_CACHE_PAD_SECONDS)
    status, deleted = _request("DELETE", f"{CONNECTIVITY}/pipelines/{name}", token=jdoe_token)
    assert status == 200, deleted


def _spicedb_parent_link_count(*, resource_type: str, resource_urn: str) -> int:
    script = textwrap.dedent(
        f"""
        import asyncio, os
        from holon_common.authz import PermissionClient

        async def main():
            client = PermissionClient(
                os.environ["HOLON_SPICEDB_URL"],
                os.environ["HOLON_SPICEDB_PRESHARED_KEY"],
                os.environ["HOLON_OPA_URL"],
            )
            try:
                links = await client.read_relationships(
                    resource_type={resource_type!r},
                    resource_urn={resource_urn!r},
                    relation="parent_workspace",
                )
                print(len(links))
            finally:
                await client.aclose()

        asyncio.run(main())
        """
    )
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "connectivity", "python", "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"spicedb read failed: {result.stderr or result.stdout}"
    return int(result.stdout.strip().splitlines()[-1])


def _pipeline_body(name: str) -> dict:
    return {
        "steps": [
            {
                "step_name": "s1",
                "input_dataset": "orders",
                "function_name": "flag_high_value_order",
                "output_dataset": f"{name}_out",
            }
        ]
    }


def test_delete_source_removes_parent_workspace(jdoe_token: str) -> None:
    name = _unique_name("rebac_del")
    status, registration = _request(
        "POST",
        f"{CONNECTIVITY}/sources",
        token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API},
    )
    assert status == 200, registration
    assert _spicedb_parent_link_count(resource_type="source", resource_urn=_source_urn(name)) == 1

    status, deleted = _request("DELETE", f"{CONNECTIVITY}/sources/{name}", token=jdoe_token)
    assert status == 200, deleted
    assert _spicedb_parent_link_count(resource_type="source", resource_urn=_source_urn(name)) == 0


def test_source_list_hides_unlinked_source(jdoe_token: str) -> None:
    name = _unique_name("rebac_list")
    status, registration = _request(
        "POST",
        f"{CONNECTIVITY}/sources",
        token=jdoe_token,
        body={"name": name, "base_url": REVIEWS_API},
    )
    assert status == 200, registration

    status, listed = _request("GET", f"{CONNECTIVITY}/sources", token=jdoe_token)
    assert status == 200, listed
    assert name in {row["name"] for row in listed}

    _spicedb_mutate(resource_type="source", resource_urn=_source_urn(name), operation="delete")
    status, listed = _request("GET", f"{CONNECTIVITY}/sources", token=jdoe_token)
    assert status == 200, listed
    assert name not in {row["name"] for row in listed}

    _spicedb_mutate(resource_type="source", resource_urn=_source_urn(name), operation="touch")
    time.sleep(_AUTHZ_CACHE_PAD_SECONDS)
    status, deleted = _request("DELETE", f"{CONNECTIVITY}/sources/{name}", token=jdoe_token)
    assert status == 200, deleted


def test_pipeline_update_keeps_workspace_and_delete_unlinks(jdoe_token: str) -> None:
    name = _unique_name("rebac_pipe_ws")
    status, created = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{name}", token=jdoe_token, body=_pipeline_body(name)
    )
    assert status in (200, 201), created
    assert created["workspace_id"] == WORKSPACE_ID

    status, updated = _request(
        "POST", f"{CONNECTIVITY}/pipelines/{name}", token=jdoe_token, body=_pipeline_body(name)
    )
    assert status in (200, 201), updated
    assert updated["workspace_id"] == WORKSPACE_ID

    status, deleted = _request("DELETE", f"{CONNECTIVITY}/pipelines/{name}", token=jdoe_token)
    assert status == 200, deleted
    assert _spicedb_parent_link_count(resource_type="pipeline", resource_urn=_pipeline_urn(name)) == 0
