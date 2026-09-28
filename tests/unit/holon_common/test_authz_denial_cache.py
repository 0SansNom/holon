"""Denial cache and SpiceDB/OPA circuit-breaker failure classes."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_common.auth import Principal  # noqa: E402
from holon_common.authz import PermissionClient, _upstream_failure  # noqa: E402
from holon_common.observability import CircuitBreaker  # noqa: E402


def _principal() -> Principal:
    return Principal(
        urn="hl:acme:global:user:jdoe",
        type="user",
        tenant_id="acme",
        display_name="Jane",
        country="FR",
    )


def _client() -> PermissionClient:
    return PermissionClient("http://spicedb", "key", "http://opa", decision_cache_ttl_seconds=60)


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://spicedb/v1/permissions/check")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("status", request=request, response=response)


def test_allows_are_not_cached_so_a_revocation_is_visible_immediately() -> None:
    client = _client()
    calls = {"n": 0}
    granted = {"value": True}

    async def rebac(*_args, **_kwargs) -> bool:
        calls["n"] += 1
        return granted["value"]

    async def abac(*_args, **_kwargs) -> bool:
        return True

    client.check_rebac = rebac  # type: ignore[method-assign]
    client.check_abac = abac  # type: ignore[method-assign]

    async def run() -> None:
        first = await client.authorize(
            _principal(), resource_type="object_type", resource_urn="hl:acme:main:object-type:Order", permission="read"
        )
        granted["value"] = False
        second = await client.authorize(
            _principal(), resource_type="object_type", resource_urn="hl:acme:main:object-type:Order", permission="read"
        )
        assert first.allowed is True
        assert second.allowed is False
        assert calls["n"] == 2
        await client.aclose()

    asyncio.run(run())


def test_denials_are_cached_until_ttl() -> None:
    client = _client()
    calls = {"n": 0}

    async def rebac(*_args, **_kwargs) -> bool:
        calls["n"] += 1
        return False

    client.check_rebac = rebac  # type: ignore[method-assign]

    async def run() -> None:
        for _ in range(3):
            decision = await client.authorize(
                _principal(),
                resource_type="object_type",
                resource_urn="hl:acme:main:object-type:Order",
                permission="read",
            )
            assert decision.allowed is False
        assert calls["n"] == 1
        await client.aclose()

    asyncio.run(run())


def test_nested_resource_attributes_are_a_stable_cache_key() -> None:
    client = _client()

    async def rebac(*_args, **_kwargs) -> bool:
        return False

    client.check_rebac = rebac  # type: ignore[method-assign]

    async def run() -> None:
        attributes = {"tags": ["a", "b"], "meta": {"nested": True}}
        await client.authorize(
            _principal(),
            resource_type="object_type",
            resource_urn="hl:acme:main:object-type:Order",
            permission="write",
            resource_attributes=attributes,
        )
        await client.authorize(
            _principal(),
            resource_type="object_type",
            resource_urn="hl:acme:main:object-type:Order",
            permission="write",
            resource_attributes={"meta": {"nested": True}, "tags": ["a", "b"]},
        )
        assert len(client._decision_cache) == 1
        await client.aclose()

    asyncio.run(run())


def test_denial_cache_evicts_the_oldest_entry() -> None:
    client = _client()
    client._denial_cache_max = 2

    async def rebac(principal_urn: str, resource_type: str, resource_urn: str, permission: str) -> bool:
        return False

    client.check_rebac = rebac  # type: ignore[method-assign]

    async def run() -> None:
        for name in ("A", "B", "C"):
            await client.authorize(
                _principal(),
                resource_type="object_type",
                resource_urn=f"hl:acme:main:object-type:{name}",
                permission="read",
            )
        urns = {key[4] for key in client._decision_cache}
        assert len(client._decision_cache) == 2
        assert "hl:acme:main:object-type:A" not in urns
        await client.aclose()

    asyncio.run(run())


def test_http_400_does_not_open_the_breaker_and_503_does() -> None:
    breaker = CircuitBreaker(name="spicedb-check", failure_threshold=5, cooldown_seconds=30.0)

    async def bad(code: int):
        raise _status_error(code)

    async def run() -> None:
        for _ in range(6):
            try:
                await breaker.call(lambda: bad(400), counts_as_failure=_upstream_failure)
            except httpx.HTTPStatusError:
                pass
        assert breaker._state == "closed"
        for _ in range(5):
            try:
                await breaker.call(lambda: bad(503), counts_as_failure=_upstream_failure)
            except httpx.HTTPStatusError:
                pass
        assert breaker._state == "open"

    asyncio.run(run())


def test_client_error_resets_the_failure_streak() -> None:
    breaker = CircuitBreaker(name="spicedb-check", failure_threshold=5, cooldown_seconds=30.0)

    async def bad(code: int):
        raise _status_error(code)

    async def run() -> None:
        for _ in range(4):
            try:
                await breaker.call(lambda: bad(503), counts_as_failure=_upstream_failure)
            except httpx.HTTPStatusError:
                pass
        assert breaker._failures == 4
        try:
            await breaker.call(lambda: bad(400), counts_as_failure=_upstream_failure)
        except httpx.HTTPStatusError:
            pass
        assert breaker._state == "closed"
        assert breaker._failures == 0
        for _ in range(4):
            try:
                await breaker.call(lambda: bad(503), counts_as_failure=_upstream_failure)
            except httpx.HTTPStatusError:
                pass
        assert breaker._state == "closed"

    asyncio.run(run())


def test_http_429_opens_the_breaker() -> None:
    breaker = CircuitBreaker(name="spicedb-check", failure_threshold=5, cooldown_seconds=30.0)

    async def bad(code: int):
        raise _status_error(code)

    async def run() -> None:
        for _ in range(5):
            try:
                await breaker.call(lambda: bad(429), counts_as_failure=_upstream_failure)
            except httpx.HTTPStatusError:
                pass
        assert breaker._state == "open"

    asyncio.run(run())
