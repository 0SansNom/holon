"""Tests for Identity event denylist + snapshot hydrate."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_common.auth import (  # noqa: E402
    is_jti_revoked,
    is_principal_disabled,
    reset_revocation_state,
)
from holon_common.events import EventEnvelope  # noqa: E402
from holon_common.principal_status import _apply_identity_auth_event  # noqa: E402


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_revocation_state()
    yield
    reset_revocation_state()


def _event(event_type: str, payload: dict) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        tenant_id="acme",
        workspace_id="main",
        aggregate_type="Principal",
        aggregate_id="hl:acme:global:user:x",
        correlation_id="c",
        partition_key="acme/x",
        producer="identity-platform@0.1.0",
        actor={"type": "user", "urn": "hl:acme:global:user:msmith"},
        payload=payload,
    )


def test_status_changed_disable_and_enable() -> None:
    _apply_identity_auth_event(
        _event("identity.principal.status_changed", {"principal_urn": "hl:acme:global:user:x", "status": "disabled"}),
        authz=None,
    )
    assert is_principal_disabled("hl:acme:global:user:x")
    _apply_identity_auth_event(
        _event("identity.principal.status_changed", {"principal_urn": "hl:acme:global:user:x", "status": "active"}),
        authz=None,
    )
    assert not is_principal_disabled("hl:acme:global:user:x")


def test_token_revoked_marks_jti() -> None:
    _apply_identity_auth_event(
        _event("identity.token.revoked", {"jti": "deadbeef", "principal_urn": "hl:acme:global:user:x"}),
        authz=None,
    )
    assert is_jti_revoked("deadbeef")


def test_permission_events_invalidate_authz_cache() -> None:
    authz = MagicMock()
    _apply_identity_auth_event(
        _event(
            "identity.permission.revoked",
            {
                "principal_urn": "hl:acme:global:user:x",
                "resource_type": "workspace",
                "resource_urn": "hl:acme:global:workspace:main",
                "relation": "viewer",
            },
        ),
        authz=authz,
    )
    authz.invalidate_principal.assert_called_once_with("hl:acme:global:user:x")
