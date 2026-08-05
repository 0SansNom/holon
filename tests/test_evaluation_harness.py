"""Phase 3 Lot C — Evaluation harness: one real `/evaluate` run (the
starter gold set, ~14 questions, plus the zero-tolerance security suite).
The single most expensive test in this suite in terms of real API calls
— deliberately isolated to its own module so it's obvious which test to
skip if conserving API budget. Requires the stack running (`make up`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

# Real, metered Anthropic calls — excluded from CI by default (cost +
# secret-exposure risk); run explicitly with `pytest -m llm`.
pytestmark = pytest.mark.llm

IDENTITY = "http://localhost:8001"
INTELLIGENCE = "http://localhost:8006"

TENANT_ID = "acme"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _token_for(principal_urn: str) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        local_name = principal_urn.rsplit(":", 1)[-1]
        status, body = _request(
            "POST",
            f"{IDENTITY}/token",
            body={"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"},
        )
        if status == 200:
            return body["access_token"]
        time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:jdoe")


def test_evaluate_runs_the_starter_gold_set_and_security_suite(jdoe_token: str) -> None:
    status, body = _request("POST", f"{INTELLIGENCE}/evaluate", token=jdoe_token)
    assert status == 200, body

    gold_set = body["goldSet"]
    assert gold_set["metrics"]["gold_set_size"] >= 10, gold_set
    assert gold_set["metrics"]["exactitude"] is not None, gold_set
    assert gold_set["metrics"]["groundedness_rate"] is not None, gold_set
    # P3.C1's own honesty requirement — this is a starter set, not a real
    # customer's annotated gold set, and the response must say so.
    assert "starter" in gold_set["disclaimer"].lower(), gold_set

    security = body["security"]
    assert security["zero_tolerance_violations"] == 0, security
    assert security["passed"] is True, security
