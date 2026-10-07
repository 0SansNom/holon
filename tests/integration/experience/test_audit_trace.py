"""One correlation id gathers the audit records every service wrote for an action."""

from __future__ import annotations

import json
import time
import urllib.request
import uuid

from conftest import EXPERIENCE, _request


def _get_with_correlation(url: str, token: str, correlation_id: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-Correlation-ID", correlation_id)
    with urllib.request.urlopen(req, timeout=30) as response:
        assert response.headers["X-Correlation-ID"] == correlation_id
        return response.status, json.loads(response.read())


def test_trace_view_gathers_audit_records_from_several_services(msmith_token: str) -> None:
    trace_id = f"it-{uuid.uuid4().hex}"
    # Each service authorizes this call and audits the decision under trace_id.
    status, _ = _get_with_correlation(f"{EXPERIENCE}/api/audit-events/trace/unused", msmith_token, trace_id)
    assert status == 200

    deadline = time.monotonic() + 20
    services: set[str] = set()
    while time.monotonic() < deadline:
        status, body = _request("GET", f"{EXPERIENCE}/api/audit-events/trace/{trace_id}", token=msmith_token)
        assert status == 200, body
        assert all(event["traceId"] == trace_id for event in body["data"]), body
        services = {event["service"] for event in body["data"]}
        if {"experience", "knowledge", "identity"} <= services:
            break
        time.sleep(1)
    assert {"experience", "knowledge", "identity"} <= services, body
    occurred = [event["occurredAt"] for event in body["data"]]
    assert occurred == sorted(occurred), body
