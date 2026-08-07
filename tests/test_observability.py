"""End-to-end verification of observability instrumentation:
Prometheus metrics, health probes, and OpenTelemetry traces in Jaeger.
Black-box over HTTP. Requires the stack running (`make up`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import JAEGER, PROMETHEUS

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


def _get_json(url: str):
    status, body = _get(url)
    return status, json.loads(body)


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


def test_prometheus_is_scraping_every_service() -> None:
    deadline = time.monotonic() + 30
    jobs_up: dict[str, str] = {}
    while time.monotonic() < deadline:
        status, body = _get_json(f"{PROMETHEUS}/api/v1/targets")
        assert status == 200, body
        jobs_up = {t["labels"]["job"]: t["health"] for t in body["data"]["activeTargets"]}
        if all(jobs_up.get(name) == "up" for name in SERVICES):
            break
        time.sleep(2)
    for name in SERVICES:
        assert jobs_up.get(name) == "up", (name, jobs_up)


def test_jaeger_has_received_traces_from_every_service() -> None:
    # Generate a bit of fresh traffic so at least one trace per service is
    # recent, then poll Jaeger's own API (not the app) for confirmation.
    for base_url in SERVICES.values():
        _get(f"{base_url}/health")

    deadline = time.monotonic() + 30
    known_services: list[str] = []
    while time.monotonic() < deadline:
        status, body = _get_json(f"{JAEGER}/api/services")
        assert status == 200, body
        known_services = body["data"] or []
        if all(f"{name}-platform" in known_services for name in SERVICES):
            break
        time.sleep(2)
    for name in SERVICES:
        assert f"{name}-platform" in known_services, known_services
