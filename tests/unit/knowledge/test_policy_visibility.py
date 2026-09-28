"""Live confidential-visibility cache. OPA is a fake; no stack."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "libs"))
sys.path.insert(0, str(REPO_ROOT / "services" / "knowledge"))

from app import policy  # noqa: E402
from holon_common.auth import Principal  # noqa: E402


def _principal(country: str | None) -> Principal:
    return Principal(
        urn="hl:acme:global:user:jdoe",
        type="user",
        tenant_id="acme",
        display_name="Jane",
        country=country,
    )


class _Authz:
    def __init__(self, answers: dict[str | None, bool] | Exception):
        self.answers = answers
        self.calls = 0

    async def check_abac(self, principal: Principal, resource: dict) -> bool:
        self.calls += 1
        assert resource == {"classification": "confidential"}
        if isinstance(self.answers, Exception):
            raise self.answers
        return self.answers[principal.country]


def setup_function() -> None:
    policy.bind_authz(None)
    policy.clear_visibility_cache()
    policy._now = lambda: 0.0


def test_unbound_authz_masks_without_caching_a_later_bind() -> None:
    assert asyncio.run(policy.confidential_visible(_principal("FR"))) is False
    authz = _Authz({"FR": True})
    policy.bind_authz(authz)
    policy._now = lambda: 0.0
    assert asyncio.run(policy.confidential_visible(_principal("FR"))) is True
    assert authz.calls == 1


def test_same_country_is_cached_until_ttl() -> None:
    authz = _Authz({"FR": True, "JP": False})
    policy.bind_authz(authz)
    clock = {"t": 0.0}
    policy._now = lambda: clock["t"]

    assert asyncio.run(policy.confidential_visible(_principal("FR"))) is True
    assert asyncio.run(policy.confidential_visible(_principal("FR"))) is True
    assert asyncio.run(policy.confidential_visible(_principal("JP"))) is False
    assert authz.calls == 2

    clock["t"] = 5.0
    authz.answers["FR"] = False
    assert asyncio.run(policy.confidential_visible(_principal("FR"))) is False
    assert authz.calls == 3


def test_opa_down_masks_and_caches_the_denial() -> None:
    authz = _Authz(RuntimeError("opa down"))
    policy.bind_authz(authz)
    assert asyncio.run(policy.confidential_visible(_principal("FR"))) is False
    assert asyncio.run(policy.confidential_visible(_principal("FR"))) is False
    assert authz.calls == 1
