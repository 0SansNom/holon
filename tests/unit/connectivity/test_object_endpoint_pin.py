"""S3 https keeps its hostname; http is dialed at the address the SSRF check saw."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))

from holon_common.connector_safety import ConnectorSafetyError, pin_object_endpoint  # noqa: E402


def _gai(mapping: dict[str, str]):
    def fake(host, *a, **k):
        name = (host or "").strip().lower().rstrip(".")
        addr = mapping.get(name)
        if addr is None:
            raise socket.gaierror("not found")
        sock_addr = (addr, 0, 0, 0) if ":" in addr else (addr, 0)
        return [(0, 0, 0, "", sock_addr)]

    return fake


def test_ibm_cos_https_endpoint_keeps_the_hostname(monkeypatch) -> None:
    """IBM COS is plain S3-compatible HTTPS — pin must not rewrite the host."""
    host = "s3.us-south.cloud-object-storage.appdomain.cloud"
    monkeypatch.setattr(
        "holon_common.connector_safety.socket.getaddrinfo",
        _gai({host: "8.8.8.8"}),
    )
    assert (
        pin_object_endpoint(f"https://{host}", kind="s3")
        == f"https://{host}"
    )


def test_s3_https_and_bare_endpoints_keep_the_hostname(monkeypatch) -> None:
    monkeypatch.setattr(
        "holon_common.connector_safety.socket.getaddrinfo",
        _gai({"files.example": "8.8.8.8", "s3.eu-west-1.amazonaws.com": "8.8.8.8"}),
    )
    assert (
        pin_object_endpoint("https://s3.eu-west-1.amazonaws.com", kind="s3")
        == "https://s3.eu-west-1.amazonaws.com"
    )
    assert pin_object_endpoint("files.example:9000", kind="s3") == "files.example:9000"


def test_s3_http_is_rewritten_to_the_checked_ip(monkeypatch) -> None:
    monkeypatch.setattr(
        "holon_common.connector_safety.socket.getaddrinfo",
        _gai({"files.example": "8.8.8.8"}),
    )
    assert pin_object_endpoint("http://files.example:9000/bucket", kind="s3") == "http://8.8.8.8:9000/bucket"


def test_azure_keeps_its_endpoint_after_the_dns_check(monkeypatch) -> None:
    monkeypatch.setattr(
        "holon_common.connector_safety.socket.getaddrinfo",
        _gai({"blob.example": "8.8.8.8"}),
    )
    assert pin_object_endpoint("https://blob.example", kind="azure") == "https://blob.example"


def test_s3_https_still_refuses_rebinding(monkeypatch) -> None:
    answers = ["8.8.8.8", "127.0.0.1"]

    def fake(host, *a, **k):
        name = (host or "").strip().lower().rstrip(".")
        if name != "files.example":
            raise socket.gaierror("not found")
        addr = answers.pop(0)
        return [(0, 0, 0, "", (addr, 0))]

    monkeypatch.setattr("holon_common.connector_safety.socket.getaddrinfo", fake)
    with pytest.raises(ConnectorSafetyError, match="DNS answer changed"):
        pin_object_endpoint("https://files.example", kind="s3")
