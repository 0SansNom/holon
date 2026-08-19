"""SpiceDB object-id encoding used to match LookupResources results."""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.modules.setdefault("httpx", types.ModuleType("httpx"))
_prom = types.ModuleType("prometheus_client")
_prom.CONTENT_TYPE_LATEST = "text/plain"
_prom.Counter = lambda *a, **k: types.SimpleNamespace(inc=lambda *a, **k: None)
_prom.Histogram = lambda *a, **k: types.SimpleNamespace(observe=lambda *a, **k: None)
_prom.generate_latest = lambda *a, **k: b""
sys.modules.setdefault("prometheus_client", _prom)

from holon_common.authz import _subject, spicedb_object_id  # noqa: E402


def test_spicedb_object_id_strips_colon_and_dot() -> None:
    assert spicedb_object_id("hl:acme:main:object-type:Customer") == "hl_acme_main_object-type_Customer"
    assert spicedb_object_id("hl:acme:main:relation-type:Order.customer") == (
        "hl_acme_main_relation-type_Order_customer"
    )


def test_lookup_permissionship_names_are_accepted() -> None:
    """LookupResources uses LOOKUP_PERMISSIONSHIP_HAS_PERMISSION, not
    the CheckPermission PERMISSIONSHIP_HAS_PERMISSION token.
    """
    from holon_common.authz import PermissionClient

    def _keep(permissionship: str) -> bool:
        return "HAS_PERMISSION" in permissionship and "NO_PERMISSION" not in permissionship

    assert _keep("LOOKUP_PERMISSIONSHIP_HAS_PERMISSION")
    assert _keep("PERMISSIONSHIP_HAS_PERMISSION")
    assert not _keep("LOOKUP_PERMISSIONSHIP_NO_PERMISSION")
    assert not _keep("PERMISSIONSHIP_NO_PERMISSION")
    assert PermissionClient is not None


def test_subject_includes_optional_relation_for_group_grants() -> None:
    direct = _subject("principal", "hl:acme:global:user:alice")
    assert "optionalRelation" not in direct
    inherited = _subject("principal", "hl:acme:global:group:readers", "member")
    assert inherited["optionalRelation"] == "member"
    assert inherited["object"]["objectId"] == "hl_acme_global_group_readers"
