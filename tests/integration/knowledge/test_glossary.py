"""Tests for Glossary."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import IDENTITY, KNOWLEDGE, ontology_url, holon_url


def _request(method: str, url: str, *, token: str | None = None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _token_for(principal_urn: str) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        local_name = principal_urn.rsplit(":", 1)[-1]
        data = json.dumps({"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"}).encode()
        req = urllib.request.Request(f"{IDENTITY}/token", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read())["access_token"]
        except urllib.error.HTTPError:
            time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


def test_glossary_is_populated_with_real_terms(jdoe_token: str) -> None:
    status, body = _request("GET", holon_url("/glossary"), token=jdoe_token)
    assert status == 200, body
    assert len(body) >= 5, body
    assert all(entry["definition"].strip() for entry in body), body


def test_glossary_term_resolves_by_canonical_name(jdoe_token: str) -> None:
    status, body = _request("GET", holon_url("/glossary/client"), token=jdoe_token)
    assert status == 200, body
    assert "Customer" in body["definition"], body


def test_glossary_term_resolves_by_synonym(jdoe_token: str) -> None:
    status, canonical = _request("GET", holon_url("/glossary/client"), token=jdoe_token)
    assert status == 200, canonical

    status, via_synonym = _request("GET", holon_url("/glossary/customer"), token=jdoe_token)
    assert status == 200, via_synonym
    assert via_synonym["definition"] == canonical["definition"], (canonical, via_synonym)


def test_glossary_lookup_is_case_insensitive(jdoe_token: str) -> None:
    status, body = _request("GET", holon_url("/glossary/CLIENT"), token=jdoe_token)
    assert status == 200, body


def test_unknown_glossary_term_is_404(jdoe_token: str) -> None:
    status, body = _request("GET", holon_url("/glossary/doesnotexist"), token=jdoe_token)
    assert status == 404, body
