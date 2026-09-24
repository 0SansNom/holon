"""Integration tests for Intelligence session/plugin/model ReBAC."""

from __future__ import annotations

import subprocess
import textwrap
import time
from pathlib import Path

from conftest import INTELLIGENCE, TENANT_ID, WORKSPACE_ID, _request, _unique_name

REPO_ROOT = Path(__file__).resolve().parents[3]
# PermissionClient decision cache TTL is 5s — wait past it after SpiceDB mutations.
_AUTHZ_CACHE_PAD_SECONDS = 6.0

# Same tiny DecisionTreeClassifier artifact as test_model_registry.py (no host joblib needed).
_MODEL_ARTIFACT_B64 = (
    "gASVQwIAAAAAAACMFXNrbGVhcm4udHJlZS5fY2xhc3Nlc5SMFkRlY2lzaW9uVHJlZUNsYXNzaWZpZXKU"
    "k5QpgZR9lCiMCWNyaXRlcmlvbpSMBGdpbmmUjAhzcGxpdHRlcpSMBGJlc3SUjAltYXhfZGVwdGiUSwKM"
    "EW1pbl9zYW1wbGVzX3NwbGl0lEsCjBBtaW5fc2FtcGxlc19sZWFmlEsBjBhtaW5fd2VpZ2h0X2ZyYWN0"
    "aW9uX2xlYWaURwAAAAAAAAAAjAxtYXhfZmVhdHVyZXOUTowObWF4X2xlYWZfbm9kZXOUTowMcmFuZG9t"
    "X3N0YXRllEsAjBVtaW5faW1wdXJpdHlfZGVjcmVhc2WURwAAAAAAAAAAjAxjbGFzc193ZWlnaHSUTowJ"
    "Y2NwX2FscGhhlEcAAAAAAAAAAIwNbW9ub3RvbmljX2NzdJROjA5uX2ZlYXR1cmVzX2luX5RLAYwKbl9v"
    "dXRwdXRzX5RLAYwIY2xhc3Nlc1+UjBNqb2JsaWIubnVtcHlfcGlja2xllIwRTnVtcHlBcnJheVdyYXBw"
    "ZXKUk5QpgZR9lCiMCHN1YmNsYXNzlIwFbnVtcHmUjAduZGFycmF5lJOUjAVzaGFwZZRLA4WUjAVvcmRl"
    "cpSMAUOUjAVkdHlwZZRoHYwFZHR5cGWUk5SMAmk4lImIh5RSlChLA4wBPJROTk5K/////0r/////SwB0"
    "lGKMCmFsbG93X21tYXCUiIwbbnVtcHlfYXJyYXlfYWxpZ25tZW50X2J5dGVzlEsQdWIB/wAAAAAAAAAA"
    "AQAAAAAAAAACAAAAAAAAAJWfAAAAAAAAAIwKbl9jbGFzc2VzX5SMFm51bXB5Ll9jb3JlLm11bHRpYXJy"
    "YXmUjAZzY2FsYXKUk5RoKUMIAwAAAAAAAACUhpRSlIwNbWF4X2ZlYXR1cmVzX5RLAYwFdHJlZV+UjBJz"
    "a2xlYXJuLnRyZWUuX3RyZWWUjARUcmVllJOUSwFoGSmBlH2UKGgcaB9oIEsBhZRoImgjaCRoKWgsiGgt"
    "SxB1Yg////////////////////8DAAAAAAAAAJWNAQAAAAAAAEsBh5RSlH2UKGgJSwKMCm5vZGVfY291"
    "bnSUSwWMBW5vZGVzlGgZKYGUfZQoaBxoH2ggSwWFlGgiaCNoJGgmjANWNjSUiYiHlFKUKEsDjAF8lE4o"
    "jApsZWZ0X2NoaWxklIwLcmlnaHRfY2hpbGSUjAdmZWF0dXJllIwJdGhyZXNob2xklIwIaW1wdXJpdHmU"
    "jA5uX25vZGVfc2FtcGxlc5SMF3dlaWdodGVkX25fbm9kZV9zYW1wbGVzlIwSbWlzc2luZ19nb190b19s"
    "ZWZ0lHSUfZQoaEloJowCaTiUiYiHlFKUKEsDaCpOTk5K/////0r/////SwB0lGJLAIaUaEpoVUsIhpRo"
    "S2hVSxCGlGhMaCaMAmY4lImIh5RSlChLA2gqTk5OSv////9K/////0sAdJRiSxiGlGhNaFxLIIaUaE5o"
    "VUsohpRoT2hcSzCGlGhQaCaMAnUxlImIh5RSlChLA2hITk5OSv////9K/////0sAdJRiSziGlHVLQEsB"
    "SxB0lGJoLIhoLUsQdWIB/wEAAAAAAAAAAgAAAAAAAAAAAAAAAAAAAAAAAAAAiMNAVlVVVVVV5T8GAAAA"
    "AAAAAAAAAAAAABhAAAAAAAAAAAD//////////////////////v////////8AAAAAAAAAwAAAAAAAAAAA"
    "AgAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAwAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAD59UAAAAAA"
    "AADgPwQAAAAAAAAAAAAAAAAAEEAAAAAAAAAAAP/////////////////////+/////////wAAAAAAAADA"
    "AAAAAAAAAAACAAAAAAAAAAAAAAAAAABAAAAAAAAAAAD//////////////////////v////////8AAAAA"
    "AAAAwAAAAAAAAAAAAgAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAlTAAAAAAAAAAjAZ2YWx1ZXOUaBkpgZR9"
    "lChoHGgfaCBLBUsBSwOHlGgiaCNoJGhcaCyIaC1LEHViBv///////1VVVVVVVdU/VVVVVVVV1T9VVVVV"
    "VVXVPwAAAAAAAPA/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA4D8AAAAAAADgPwAAAAAAAAAA"
    "AAAAAAAA8D8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADwP5UgAAAAAAAAAHVijBBfc2tsZWFy"
    "bl92ZXJzaW9ulIwFMS41LjKUdWIu"
)

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"lifetimeValue": {"type": "number"}},
    "required": ["lifetimeValue"],
}


def _spicedb_mutate(*, resource_type: str, resource_urn: str, operation: str) -> None:
    """Touch/delete parent_workspace from inside Intelligence (has the compose SpiceDB key)."""
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
        ["docker", "compose", "exec", "-T", "intelligence", "python", "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"spicedb mutate failed: {result.stderr or result.stdout}"


def _ml_model_urn(name: str) -> str:
    return f"hl:{TENANT_ID}:global:ml-model:{name}"


def test_create_session_seeds_rebac_and_unlink_denies_get(jdoe_token: str) -> None:
    status, session = _request("POST", f"{INTELLIGENCE}/sessions", token=jdoe_token)
    assert status == 200, session
    session_urn = session["urn"]

    status, got = _request("GET", f"{INTELLIGENCE}/sessions/{session_urn}", token=jdoe_token)
    assert status == 200, got

    _spicedb_mutate(resource_type="agent_session", resource_urn=session_urn, operation="delete")
    time.sleep(_AUTHZ_CACHE_PAD_SECONDS)

    status, body = _request("GET", f"{INTELLIGENCE}/sessions/{session_urn}", token=jdoe_token)
    assert status == 403, body

    _spicedb_mutate(resource_type="agent_session", resource_urn=session_urn, operation="touch")
    time.sleep(_AUTHZ_CACHE_PAD_SECONDS)
    status, got = _request("GET", f"{INTELLIGENCE}/sessions/{session_urn}", token=jdoe_token)
    assert status == 200, got


def test_model_get_denied_after_parent_workspace_unlinked(jdoe_token: str) -> None:
    name = _unique_name("rebac-model")
    status, registration = _request(
        "POST",
        f"{INTELLIGENCE}/models/{name}",
        token=jdoe_token,
        body={
            "version": "1.0.0",
            "framework": "sklearn",
            "artifact_base64": _MODEL_ARTIFACT_B64,
            "input_schema": _INPUT_SCHEMA,
        },
    )
    assert status == 200, registration

    status, got = _request("GET", f"{INTELLIGENCE}/models/{name}", token=jdoe_token)
    assert status == 200, got

    _spicedb_mutate(resource_type="ml_model", resource_urn=_ml_model_urn(name), operation="delete")
    time.sleep(_AUTHZ_CACHE_PAD_SECONDS)

    status, body = _request("GET", f"{INTELLIGENCE}/models/{name}", token=jdoe_token)
    assert status == 403, body

    status, listed = _request("GET", f"{INTELLIGENCE}/models", token=jdoe_token)
    assert status == 200, listed
    assert name not in {row["name"] for row in listed}

    _spicedb_mutate(resource_type="ml_model", resource_urn=_ml_model_urn(name), operation="touch")
    time.sleep(_AUTHZ_CACHE_PAD_SECONDS)
    status, got = _request("GET", f"{INTELLIGENCE}/models/{name}", token=jdoe_token)
    assert status == 200, got
