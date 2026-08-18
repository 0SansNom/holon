"""In-process cache for compiled ontology definitions (SAS R7.7).

The conceptual ontology changes on a human timescale (publish, not
per-request). Every object read currently re-fetches the same
`object_type` row. This cache is the first of the three mitigations
in SAS §7.4: keep compiled definitions in memory and invalidate on
write. It is per-process on purpose — Knowledge is a single modulith
replica in the bootstrap topology (ADR 023). Write paths MUST call
`invalidate_object_type` so a publish is visible on the next read
in the same process.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Optional

_DEFAULT_TTL_SECONDS = 30.0
_store: dict[str, tuple[Any, float]] = {}


def _now() -> float:
    return time.monotonic()


def has(key: str) -> bool:
    item = _store.get(key)
    if item is None:
        return False
    _, expires_at = item
    if _now() >= expires_at:
        _store.pop(key, None)
        return False
    return True


def cached_keys() -> set[str]:
    return {key for key in list(_store) if has(key)}


def get(key: str) -> Optional[Any]:
    item = _store.get(key)
    if item is None:
        return None
    value, expires_at = item
    if _now() >= expires_at:
        _store.pop(key, None)
        return None
    return copy.deepcopy(value)


def put(key: str, value: Any, *, ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> None:
    _store[key] = (copy.deepcopy(value), _now() + ttl_seconds)


def invalidate(*keys: str) -> None:
    for key in keys:
        _store.pop(key, None)


def invalidate_prefix(prefix: str) -> None:
    for key in [key for key in _store if key.startswith(prefix)]:
        _store.pop(key, None)


def object_type_key(urn: str) -> str:
    return f"ot:{urn}"


def object_type_dataset_key(tenant_id: str, source_dataset_urn: str) -> str:
    return f"ot-ds:{tenant_id}:{source_dataset_urn}"


def object_type_list_key(tenant_id: str) -> str:
    return f"ot-list:{tenant_id}"


def property_classifications_key(urn: str) -> str:
    return f"ot-props:{urn}"


def invalidate_object_type(*, urn: str, tenant_id: str | None = None) -> None:
    """Drop every cached view of one ObjectType, plus the tenant list."""
    invalidate(object_type_key(urn), property_classifications_key(urn))
    if tenant_id:
        invalidate_prefix(f"ot-ds:{tenant_id}:")
        invalidate(object_type_list_key(tenant_id))
    else:
        # Dataset and list keys are tenant-scoped; without a tenant, drop
        # every list/dataset entry so a publish cannot serve a stale row.
        invalidate_prefix("ot-ds:")
        invalidate_prefix("ot-list:")


def clear() -> None:
    _store.clear()
