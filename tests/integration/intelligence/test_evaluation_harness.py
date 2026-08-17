"""Tests for Evaluation Harness."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, INTELLIGENCE, _request

# Real, metered Anthropic calls
# secret-exposure risk); run explicitly with `pytest -m llm`.
pytestmark = pytest.mark.llm


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


def test_evaluate_runs_the_starter_gold_set_and_security_suite(jdoe_token: str) -> None:
    status, body = _request("POST", f"{INTELLIGENCE}/evaluate", token=jdoe_token)
    assert status == 200, body

    gold_set = body["goldSet"]
    assert gold_set["metrics"]["gold_set_size"] >= 10, gold_set
    assert gold_set["metrics"]["exactitude"] is not None, gold_set
    assert gold_set["metrics"]["groundedness_rate"] is not None, gold_set
    # Starter set disclaimer check.
    assert "starter" in gold_set["disclaimer"].lower(), gold_set

    security = body["security"]
    assert security["zero_tolerance_violations"] == 0, security
    assert security["passed"] is True, security
