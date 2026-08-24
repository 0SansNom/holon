"""Tests for the SAML SP endpoints that don't require a real IdP: SP
metadata generation (always available) and the disabled-by-default
gating on /saml/login and /saml/acs. Full assertion validation is
exercised by python3-saml itself (a vetted, widely used library) plus
tests/unit/identity/test_saml.py's coverage of the claims-normalization
glue around it — a real signed-assertion round trip needs a real IdP,
which isn't part of this compose stack."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from xml.etree import ElementTree

from conftest import IDENTITY


def _raw_request(method: str, path: str, *, body: dict | None = None) -> tuple[int, bytes, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{IDENTITY}{path}", data=data, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def test_saml_metadata_is_well_formed_xml_and_matches_configured_urls() -> None:
    status, raw, headers = _raw_request("GET", "/saml/metadata")
    assert status == 200, raw
    assert "xml" in headers.get("Content-Type", "")
    root = ElementTree.fromstring(raw)
    assert root.tag.endswith("EntityDescriptor")
    xml = raw.decode()
    assert "AssertionConsumerService" in xml


def test_saml_login_404s_when_idp_not_configured() -> None:
    status, raw, _ = _raw_request("GET", "/saml/login")
    assert status == 404, raw
    body = json.loads(raw)
    assert body["errorName"] == "SamlNotConfigured"


def test_saml_acs_404s_when_idp_not_configured() -> None:
    status, raw, _ = _raw_request("POST", "/saml/acs")
    assert status == 404, raw
    body = json.loads(raw)
    assert body["errorName"] == "SamlNotConfigured"
