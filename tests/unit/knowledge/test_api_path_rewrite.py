"""Unit tests for Knowledge `/api/...` path rewrite (no stack)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO / "services" / "knowledge"
LIBS = REPO / "libs"


def _import_rewrite():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    api_pkg = types.ModuleType("app.api")
    api_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "api")]
    sys.modules["app.api"] = api_pkg
    from app.api.path_rewrite import _alias_paging_query, _rewrite_suffix  # noqa: E402

    return _rewrite_suffix, _alias_paging_query


_rewrite_suffix, _alias_paging_query = _import_rewrite()


def test_object_types_and_objects_suffix() -> None:
    assert _rewrite_suffix("/objectTypes") == "/ontology"
    assert _rewrite_suffix("/objectTypes/Customer") == "/ontology/Customer"
    assert _rewrite_suffix("/objects/Customer") == "/objects/Customer"
    assert _rewrite_suffix("/objects/Customer/1/links/orders") == "/objects/Customer/1/links/orders"


def test_link_types_and_value_types() -> None:
    assert _rewrite_suffix("/linkTypes") == "/relation-types"
    assert _rewrite_suffix("/linkTypes/orders") == "/relation-types/orders"
    assert _rewrite_suffix("/valueTypes/email") == "/value-types/email"
    assert _rewrite_suffix("/interfaceTypes") == "/interfaces"
    assert _rewrite_suffix("/sharedPropertyTypes") == "/shared-property-types"
    assert _rewrite_suffix("/actionTypes") == "/action-types"
    assert _rewrite_suffix("/objectSets/foo") == "/object-sets/foo"


def test_paging_query_aliases() -> None:
    out = _alias_paging_query(b"pageSize=25&pageToken=abc&foo=1")
    assert b"page_size=25" in out
    assert b"cursor=abc" in out
    assert b"foo=1" in out


def test_actions_suffix_not_rewritten() -> None:
    """Action preview/apply/batch stay on the public /api/ontologies path."""
    assert _rewrite_suffix("/actions/Customer.setModerationStatus") is None
    assert _rewrite_suffix("/actions/Customer.setModerationStatus/preview") is None
    assert _rewrite_suffix("/actions/Customer.setModerationStatus/batch") is None
    assert _rewrite_suffix("/objects/Customer/1/actions/x/preview") == "/objects/Customer/1/actions/x/preview"
