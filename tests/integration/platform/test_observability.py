"""Tests for Observability."""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

SERVICES = {
    "identity": "http://localhost:8001",
    "connectivity": "http://localhost:8002",
    "knowledge": "http://localhost:8003",
    "experience": "http://localhost:8004",
    "automation": "http://localhost:8005",
}


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


@pytest.mark.parametrize("name,base_url", SERVICES.items())
def test_health_ready_live_are_distinct_routes(name: str, base_url: str) -> None:
    for path in ("/health", "/ready", "/live"):
        status, _ = _get(f"{base_url}{path}")
        assert status == 200, (name, path)


@pytest.mark.parametrize("name,base_url", SERVICES.items())
def test_metrics_endpoint_exposes_prometheus_text_format(name: str, base_url: str) -> None:
    status, body = _get(f"{base_url}/metrics")
    assert status == 200, name
    text = body.decode()
    assert "http_requests_total" in text, (name, text[:500])
    assert "http_request_duration_seconds" in text, (name, text[:500])
