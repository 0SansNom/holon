"""Unit tests for holon_sdk.HolonClient (stdlib HTTP helper)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from holon_sdk import HolonClient


class _FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return json.dumps(self._body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_request_returns_json_body() -> None:
    client = HolonClient(identity_url="http://identity")
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200, {"ok": True})) as opened:
        status, body = client.request("GET", "http://knowledge/api/x", token="t")
    assert status == 200
    assert body == {"ok": True}
    req = opened.call_args[0][0]
    assert req.get_header("Authorization") == "Bearer t"


def test_token_for_retries_until_200() -> None:
    client = HolonClient(identity_url="http://identity")
    responses = [
        (503, {"error": "warming"}),
        (200, {"access_token": "abc"}),
    ]

    def fake_request(method, url, *, token=None, body=None):
        return responses.pop(0)

    with patch.object(client, "request", side_effect=fake_request), patch("time.sleep"):
        token = client.token_for("hl:acme:global:user:jdoe", timeout=5.0)
    assert token == "abc"


def test_token_for_times_out() -> None:
    client = HolonClient(identity_url="http://identity")
    with (
        patch.object(client, "request", return_value=(401, {"error": "nope"})),
        patch("time.sleep"),
        patch("time.monotonic", side_effect=[0.0, 0.0, 100.0]),
    ):
        with pytest.raises(TimeoutError):
            client.token_for("hl:acme:global:user:jdoe", timeout=1.0)
