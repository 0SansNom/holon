"""Unit tests for the Salesforce SOQL connector.

Covers config validation, cursor rewriting, nextRecordsUrl same-origin
join, and token mint + query pagination (httpx mocked). Live Salesforce
is not in the compose stack.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.salesforce_source_registry import (  # noqa: E402
    SourceConfigError,
    SourceFetchError,
    _apply_cursor,
    _next_page_url,
    _normalize_api_version,
    _normalize_login_url,
    _require_soql,
    _strip_attributes,
)


def test_normalize_login_url_defaults_and_strips_slash() -> None:
    with patch("app.salesforce_source_registry.assert_http_url"):
        assert _normalize_login_url("") == "https://login.salesforce.com"
        assert _normalize_login_url("https://test.salesforce.com/") == "https://test.salesforce.com"


def test_normalize_login_url_rejects_non_http() -> None:
    with pytest.raises(SourceConfigError):
        _normalize_login_url("ftp://login.salesforce.com")


def test_normalize_api_version() -> None:
    assert _normalize_api_version("v59.0") == "v59.0"
    with pytest.raises(SourceConfigError, match="api_version"):
        _normalize_api_version("59.0")


def test_require_soql_must_be_select() -> None:
    assert _require_soql("  SELECT Id FROM Account  ").startswith("SELECT")
    assert _require_soql("SELECT Id FROM Account;") == "SELECT Id FROM Account"
    with pytest.raises(SourceConfigError, match="SELECT"):
        _require_soql("DELETE FROM Account")
    with pytest.raises(SourceConfigError, match="semicolon"):
        _require_soql("SELECT Id FROM Account; DELETE FROM Account")


def test_apply_cursor_appends_where_or_and() -> None:
    # DateTime cursor: SOQL requires an unquoted literal here.
    assert (
        _apply_cursor("SELECT Id FROM Account", "SystemModstamp", "2024-01-01T00:00:00Z")
        == "SELECT Id FROM Account WHERE SystemModstamp > 2024-01-01T00:00:00Z"
    )
    assert (
        _apply_cursor(
            "SELECT Id FROM Account WHERE Name != null ORDER BY Name",
            "SystemModstamp",
            "2024-01-01T00:00:00Z",
        )
        == "SELECT Id FROM Account WHERE Name != null AND SystemModstamp > 2024-01-01T00:00:00Z ORDER BY Name"
    )


def test_apply_cursor_datetime_variants_are_unquoted() -> None:
    # Bare date stays a Date literal.
    assert (
        _apply_cursor("SELECT Id FROM Account", "LastModifiedDate", "2024-01-01")
        == "SELECT Id FROM Account WHERE LastModifiedDate > 2024-01-01"
    )
    # Fractional seconds are dropped (cursor moves back < 1s: re-read, never skip).
    assert (
        _apply_cursor("SELECT Id FROM Account", "SystemModstamp", "2024-01-01T12:34:56.789Z")
        == "SELECT Id FROM Account WHERE SystemModstamp > 2024-01-01T12:34:56Z"
    )
    # Offsets are normalized to UTC 'Z'.
    assert (
        _apply_cursor("SELECT Id FROM Account", "SystemModstamp", "2024-01-01T12:34:56+02:00")
        == "SELECT Id FROM Account WHERE SystemModstamp > 2024-01-01T10:34:56Z"
    )


def test_apply_cursor_accepts_the_format_salesforce_actually_returns() -> None:
    # REST query JSON returns DateTime fields as ...000+0000 (no colon in the
    # offset); that value is what gets stored as last_cursor_value.
    assert (
        _apply_cursor("SELECT Id FROM Account", "SystemModstamp", "2024-01-15T10:30:00.000+0000")
        == "SELECT Id FROM Account WHERE SystemModstamp > 2024-01-15T10:30:00Z"
    )


def test_apply_cursor_non_iso_string_stays_quoted_and_escaped() -> None:
    # Plain string cursor (e.g. a Salesforce record Id) must remain quoted.
    assert (
        _apply_cursor("SELECT Id FROM Account", "Id", "001A000001abcDE")
        == "SELECT Id FROM Account WHERE Id > '001A000001abcDE'"
    )
    # Quotes/backslashes in a non-ISO string cursor are still escaped.
    assert (
        _apply_cursor("SELECT Id FROM Account", "Name", "O'Brien")
        == "SELECT Id FROM Account WHERE Name > 'O\\'Brien'"
    )
    # A string that merely starts with digits but isn't a valid ISO date is quoted.
    assert (
        _apply_cursor("SELECT Id FROM Account", "Name", "2024-13-99")
        == "SELECT Id FROM Account WHERE Name > '2024-13-99'"
    )


def test_strip_attributes_drops_salesforce_metadata() -> None:
    assert _strip_attributes({"Id": "001", "attributes": {"type": "Account"}, "Name": "Acme"}) == {
        "Id": "001",
        "Name": "Acme",
    }


def test_next_page_url_joins_relative_path_to_instance() -> None:
    with patch("app.salesforce_source_registry.assert_http_url"):
        url = _next_page_url(
            instance_url="https://na1.salesforce.com",
            next_records_url="/services/data/v59.0/query/01gxx",
        )
    assert url == "https://na1.salesforce.com/services/data/v59.0/query/01gxx"


def test_next_page_url_rejects_cross_origin() -> None:
    with pytest.raises(SourceFetchError, match="different origin"):
        _next_page_url(
            instance_url="https://na1.salesforce.com",
            next_records_url="https://evil.example/services/data/v59.0/query/x",
        )


def test_register_connection_defaults_login_url() -> None:
    from app.salesforce_source_registry import register_connection

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()

    async def _get_connection(*_a, **_k):
        return {
            "name": "sf1",
            "login_url": "https://login.salesforce.com",
            "client_id": "cid",
            "has_client_secret": True,
            "instance_url": None,
        }

    with (
        patch("app.salesforce_source_registry.assert_http_url"),
        patch("app.salesforce_source_registry.assert_connector_secret_ref"),
        patch("app.salesforce_source_registry.assert_no_inline_connector_secret"),
        patch("app.salesforce_source_registry.assert_production_requires_secret_ref"),
        patch("app.salesforce_source_registry.get_connection", side_effect=_get_connection),
    ):
        result = asyncio.run(
            register_connection(
                pool,
                tenant_id="t1",
                name="sf1",
                client_id="cid",
                created_by_urn="urn:jdoe",
                secret_ref="env:SF_SECRET",
            )
        )
    assert result["name"] == "sf1"
    insert_args = pool.execute.call_args.args
    assert insert_args[3] == "https://login.salesforce.com"


def test_fetch_for_dataset_mints_token_and_paginates() -> None:
    from app.salesforce_source_registry import fetch_for_dataset

    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        side_effect=[
            {
                "connection_name": "sf1",
                "soql": "SELECT Id, Name FROM Account",
                "api_version": "v59.0",
                "cursor_property": None,
                "last_cursor_value": None,
            },
            {
                "login_url": "https://login.salesforce.com",
                "client_id": "cid",
                "client_secret": "sec",
                "secret_ref": None,
                "oauth2_cached_token": None,
                "oauth2_token_expires_at": None,
                "instance_url": None,
            },
        ]
    )
    pool.execute = AsyncMock()

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {
        "access_token": "tok",
        "instance_url": "https://na1.salesforce.com",
        "expires_in": 3600,
    }
    token_response.text = ""

    page1 = MagicMock()
    page1.status_code = 200
    page1.json.return_value = {
        "done": False,
        "nextRecordsUrl": "/services/data/v59.0/query/next1",
        "records": [
            {"attributes": {"type": "Account"}, "Id": "001A", "Name": "A"},
        ],
    }
    page1.text = ""

    page2 = MagicMock()
    page2.status_code = 200
    page2.json.return_value = {
        "done": True,
        "records": [
            {"attributes": {"type": "Account"}, "Id": "001B", "Name": "B"},
        ],
    }
    page2.text = ""

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=token_response)
    client.get = AsyncMock(side_effect=[page1, page2])

    with (
        patch("app.salesforce_source_registry.assert_http_url"),
        patch("app.salesforce_source_registry.httpx.AsyncClient", return_value=client),
    ):
        rows, commit = asyncio.run(fetch_for_dataset(pool, "t1", "sf_accounts"))

    assert rows == [{"Id": "001A", "Name": "A"}, {"Id": "001B", "Name": "B"}]
    assert commit is None
    assert client.get.await_count == 2
    # token cache write only (cursor commit deferred until after Iceberg write)
    assert pool.execute.await_count >= 1
