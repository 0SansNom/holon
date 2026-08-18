"""Unit tests for ReBAC/ABAC/marking search filter construction (R8.6)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "libs"))
sys.path.insert(0, str(REPO_ROOT / "services" / "knowledge"))

sys.modules.setdefault("httpx", types.ModuleType("httpx"))

from app.search import (  # noqa: E402
    _entitlement_tokens_for,
    _marking_filter,
    _principal_marking_tokens,
    _principal_tokens,
    _rebac_object_type_filter,
    _required_marking_tokens,
)
from holon_common.auth import Principal  # noqa: E402


def test_confidential_documents_get_country_tokens_not_public_read() -> None:
    tokens = _entitlement_tokens_for("confidential", {"FR", "DE"})
    assert tokens == ["country:DE", "country:FR"]
    assert "public-read" not in tokens


def test_non_confidential_documents_are_public_read() -> None:
    assert _entitlement_tokens_for("internal", {"FR"}) == ["public-read"]


def test_principal_tokens_include_country_when_set() -> None:
    principal = Principal(
        urn="hl:acme:global:user:jdoe", type="user", tenant_id="acme", display_name="Jane", country="FR"
    )
    assert "public-read" in _principal_tokens(principal)
    assert "country:FR" in _principal_tokens(principal)


def test_rebac_filter_is_object_type_terms() -> None:
    clause = _rebac_object_type_filter(["Customer", "Order"])
    assert clause == {"terms": {"object_type": ["Customer", "Order"]}}


def test_required_marking_tokens_are_prefixed() -> None:
    assert _required_marking_tokens(["pii", "export"]) == ["mark:pii", "mark:export"]
    assert _principal_marking_tokens([]) == ["mark:__none__"]


def test_marking_filter_allows_unmarked_or_fully_held() -> None:
    clause = _marking_filter(["mark:pii"])
    should = clause["bool"]["should"]
    assert any("must_not" in (item.get("bool") or {}) for item in should)
    terms_set = next(item["terms_set"] for item in should if "terms_set" in item)
    assert terms_set["required_markings"]["minimum_should_match_field"] == "required_marking_count"
    assert terms_set["required_markings"]["terms"] == ["mark:pii"]
