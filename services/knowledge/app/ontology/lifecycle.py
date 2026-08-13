"""Lifecycle status and deprecation metadata helpers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

LifecycleTarget = Literal["object_type", "property", "registry"]

VALID_LIFECYCLE_STATUSES = frozenset({
    "experimental",
    "active",
    "deprecated",
    "example",
    "promoted",
})
PROPERTY_LIFECYCLE_STATUSES = frozenset({"experimental", "active", "deprecated", "example"})
REGISTRY_LIFECYCLE_STATUSES = PROPERTY_LIFECYCLE_STATUSES
OBJECT_TYPE_LIFECYCLE_STATUSES = VALID_LIFECYCLE_STATUSES
NON_DELETABLE_OBJECT_TYPE_STATUSES = frozenset({"active", "promoted"})


def normalize_lifecycle_status(lifecycle_status: str) -> str:
    if lifecycle_status not in VALID_LIFECYCLE_STATUSES:
        raise ValueError(
            f"invalid lifecycle_status: {lifecycle_status!r} "
            f"(must be one of {sorted(VALID_LIFECYCLE_STATUSES)})"
        )
    return lifecycle_status


def assert_lifecycle_for_target(lifecycle_status: str, *, target: LifecycleTarget) -> str:
    """Validate a status is allowed on the given ontology target."""
    status = normalize_lifecycle_status(lifecycle_status)
    if status == "promoted" and target != "object_type":
        raise ValueError(
            f"lifecycle_status 'promoted' is only valid on ObjectType, not {target!r}"
        )
    if target == "property" and status not in PROPERTY_LIFECYCLE_STATUSES:
        raise ValueError(
            f"property lifecycle_status must be one of {sorted(PROPERTY_LIFECYCLE_STATUSES)}"
        )
    if target == "registry" and status not in REGISTRY_LIFECYCLE_STATUSES:
        raise ValueError(
            f"lifecycle_status must be one of {sorted(REGISTRY_LIFECYCLE_STATUSES)}"
        )
    return status


def _parse_deadline(raw: Any) -> Optional[date]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        cleaned = raw.strip()
        if not cleaned:
            return None
        # Accept YYYY-MM-DD or full ISO datetime.
        try:
            return date.fromisoformat(cleaned[:10])
        except ValueError as exc:
            raise ValueError(
                f"deprecation_deadline must be an ISO date (YYYY-MM-DD), got {raw!r}"
            ) from exc
    raise ValueError(f"deprecation_deadline must be an ISO date, got {type(raw).__name__}")


def normalize_deprecation_metadata(
    lifecycle_status: str,
    *,
    deprecation_reason: Optional[str] = None,
    deprecation_deadline: Any = None,
    replacement_urn: Optional[str] = None,
    target: LifecycleTarget = "registry",
) -> dict[str, Any]:
    """Return `{lifecycle_status, deprecation_reason, deprecation_deadline, replacement_urn}`.

    When status is deprecated, reason and deadline are required (Foundry
    prompts for both); replacement is optional. Otherwise all three are
    cleared so stale deprecate metadata cannot linger after reactivation.
    """
    status = assert_lifecycle_for_target(lifecycle_status, target=target)
    if status != "deprecated":
        return {
            "lifecycle_status": status,
            "deprecation_reason": None,
            "deprecation_deadline": None,
            "replacement_urn": None,
        }

    reason = (deprecation_reason or "").strip()
    if not reason:
        raise ValueError("deprecation_reason is required when lifecycle_status is deprecated")
    if len(reason) > 2000:
        raise ValueError("deprecation_reason is too long (max 2000 characters)")

    deadline = _parse_deadline(deprecation_deadline)
    if deadline is None:
        raise ValueError("deprecation_deadline is required when lifecycle_status is deprecated")

    repl = (replacement_urn or "").strip() or None
    if repl is not None and len(repl) > 512:
        raise ValueError("replacement_urn is too long (max 512 characters)")

    return {
        "lifecycle_status": status,
        "deprecation_reason": reason,
        "deprecation_deadline": deadline,
        "replacement_urn": repl,
    }
