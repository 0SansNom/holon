"""Deep /ready probes — Postgres plus the data-plane a service actually uses.

`/live` stays a process liveness no-op. `/ready` fails closed (HTTP 503)
when a required dependency cannot be reached within a short timeout, so
Kubernetes stops sending traffic. Checks run concurrently.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Optional, Sequence
from urllib.parse import quote, urlparse

import httpx

from .errors import HolonError

_TIMEOUT_SECONDS = 1.5


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    error: Optional[str] = None


def _err(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:240]


async def check_postgres(pool: Any, *, timeout: float = _TIMEOUT_SECONDS) -> CheckResult:
    try:
        await asyncio.wait_for(pool.fetchval("SELECT 1"), timeout=timeout)
        return CheckResult("postgres", True)
    except Exception as exc:
        return CheckResult("postgres", False, _err(exc))


async def _http(
    name: str,
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    json_body: Any = None,
    auth: Optional[tuple[str, str]] = None,
    timeout: float = _TIMEOUT_SECONDS,
) -> CheckResult:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=timeout)) as client:
            response = await client.request(method, url, headers=headers, json=json_body, auth=auth)
            response.raise_for_status()
        return CheckResult(name, True)
    except Exception as exc:
        return CheckResult(name, False, _err(exc))


async def check_spicedb(url: str, preshared_key: str, *, timeout: float = _TIMEOUT_SECONDS) -> CheckResult:
    return await _http(
        "spicedb",
        "POST",
        f"{url.rstrip('/')}/v1/schema/read",
        headers={"Authorization": f"Bearer {preshared_key}"},
        json_body={},
        timeout=timeout,
    )


async def check_opa(url: str, *, timeout: float = _TIMEOUT_SECONDS) -> CheckResult:
    return await _http("opa", "GET", f"{url.rstrip('/')}/health", timeout=timeout)


async def check_opensearch(url: str, password: str, *, timeout: float = _TIMEOUT_SECONDS) -> CheckResult:
    return await _http(
        "opensearch",
        "GET",
        f"{url.rstrip('/')}/_cluster/health",
        auth=("admin", password),
        timeout=timeout,
    )


async def check_iceberg_catalog(catalog_uri: str, warehouse: str, *, timeout: float = _TIMEOUT_SECONDS) -> CheckResult:
    encoded = quote(warehouse, safe="")
    return await _http(
        "iceberg",
        "GET",
        f"{catalog_uri.rstrip('/')}/v1/config?warehouse={encoded}",
        timeout=timeout,
    )


async def check_qdrant(url: str, *, timeout: float = _TIMEOUT_SECONDS) -> CheckResult:
    return await _http("qdrant", "GET", f"{url.rstrip('/')}/readyz", timeout=timeout)


def first_kafka_broker(bootstrap: str) -> tuple[str, int]:
    first = bootstrap.split(",")[0].strip()
    parsed = urlparse(first if "://" in first else f"tcp://{first}")
    host, port = parsed.hostname, parsed.port
    if not host or port is None:
        raise ValueError(f"invalid kafka bootstrap: {bootstrap!r}")
    return host, port


async def check_kafka_bootstrap(bootstrap: str, *, timeout: float = _TIMEOUT_SECONDS) -> CheckResult:
    try:
        host, port = first_kafka_broker(bootstrap)
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return CheckResult("kafka", True)
    except Exception as exc:
        return CheckResult("kafka", False, _err(exc))


async def check_kafka_producer(producer: Any, *, timeout: float = _TIMEOUT_SECONDS) -> CheckResult:
    ping = getattr(producer, "ping", None)
    if ping is None:
        return CheckResult("kafka", False, "producer has no ping()")
    try:
        await asyncio.wait_for(ping(timeout=timeout), timeout=timeout + 0.2)
        return CheckResult("kafka", True)
    except Exception as exc:
        return CheckResult("kafka", False, _err(exc))


async def report_ready(
    checks: Sequence[Awaitable[CheckResult]],
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Run checks concurrently. All-ok → `{status: ok, checks: …}`. Else 503."""
    results = await asyncio.gather(*checks)
    checks_map = {r.name: "ok" if r.ok else (r.error or "failed") for r in results}
    failed = [r.name for r in results if not r.ok]
    payload: dict[str, Any] = {"status": "ok", "checks": checks_map}
    if extra:
        payload.update(extra)
    if failed:
        raise HolonError.unavailable(
            "NotReady",
            "dependency check failed",
            failed=failed,
            checks=checks_map,
        )
    return payload
