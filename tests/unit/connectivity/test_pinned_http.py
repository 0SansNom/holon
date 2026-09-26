"""A connector HTTP sync is refused when DNS flips to loopback."""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.pinned_http import pinned_transport  # noqa: E402
from holon_common.connector_safety import ConnectorSafetyError  # noqa: E402


def test_http_get_refuses_dns_rebinding(monkeypatch) -> None:
    answers = ["8.8.8.8", "127.0.0.1"]

    def fake(host, *a, **k):
        name = (host or "").strip().lower().rstrip(".")
        if name != "evil.example":
            raise socket.gaierror("not found")
        if not answers:
            raise socket.gaierror("no more answers")
        addr = answers.pop(0)
        return [(0, 0, 0, "", (addr, 0))]

    monkeypatch.setattr("holon_common.connector_safety.socket.getaddrinfo", fake)

    async def _get() -> None:
        async with httpx.AsyncClient(transport=pinned_transport()) as client:
            await client.get("http://evil.example/secret")

    with pytest.raises(ConnectorSafetyError, match="DNS answer changed"):
        asyncio.run(_get())
