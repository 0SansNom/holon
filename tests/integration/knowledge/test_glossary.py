"""Tests for Glossary."""

from __future__ import annotations

from conftest import _request, holon_url


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
