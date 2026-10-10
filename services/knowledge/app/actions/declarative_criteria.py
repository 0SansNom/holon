"""Submission-criteria evaluation for declarative Action Types."""
from __future__ import annotations

from typing import Any, Optional

from holon_common import HolonError, Principal


class ActionValidationError(Exception):
    """Declarative Action failed parameter / criteria validation.

    Carry the full validation report so HTTP handlers can emit
    ``ActionValidationFailed`` with ``parameters.validation``.
    """

    def __init__(self, report: dict) -> None:
        self.report = report
        messages = report.get("messages") or []
        detail = messages[0] if messages else "action validation failed"
        super().__init__(detail)

    def to_holon_error(self) -> HolonError:
        return HolonError.invalid_argument(
            "ActionValidationFailed",
            str(self),
            validation={
                "result": self.report.get("result"),
                "parameters": self.report.get("parameters") or {},
                "submissionCriteriaResult": self.report.get("submissionCriteriaResult"),
                "messages": self.report.get("messages") or [],
            },
        )


_OPERATORS = {
    "eq": lambda actual, expected: actual == expected,
    "neq": lambda actual, expected: actual != expected,
    "gt": lambda actual, expected: actual is not None and actual > expected,
    "gte": lambda actual, expected: actual is not None and actual >= expected,
    "lt": lambda actual, expected: actual is not None and actual < expected,
    "lte": lambda actual, expected: actual is not None and actual <= expected,
    "in": lambda actual, expected: actual in (expected or []),
}

def _object_type_and_instance_id_from_instance_urn(instance_urn: str) -> tuple[str, str]:
    """Every `instance_urn` this package builds already has the
    `{ObjectType}/{id}` shape in its final segment.
    """
    local = instance_urn.rsplit(":", 1)[-1]
    object_type, instance_id = local.split("/", 1)
    return object_type, instance_id

def _evaluate_criteria(
    instance_row: dict,
    criteria: list[dict],
    *,
    principal: Optional[Principal] = None,
) -> Optional[str]:
    """Evaluate submission criteria. Flat list = implicit AND.

    Leaf kinds: property comparison, principal field comparison.
    Groups: ``all`` / ``any``. Optional ``message`` overrides the default
    failure string.
    """
    for criterion in criteria:
        error = _evaluate_one_criterion(instance_row, criterion, principal=principal)
        if error is not None:
            return error
    return None

def _evaluate_one_criterion(
    instance_row: dict,
    criterion: dict,
    *,
    principal: Optional[Principal],
) -> Optional[str]:
    custom = criterion.get("message")

    if "all" in criterion:
        for child in criterion["all"]:
            err = _evaluate_one_criterion(instance_row, child, principal=principal)
            if err is not None:
                return custom or err
        return None
    if "any" in criterion:
        errors: list[str] = []
        for child in criterion["any"]:
            err = _evaluate_one_criterion(instance_row, child, principal=principal)
            if err is None:
                return None
            errors.append(err)
        return custom or (errors[0] if errors else "submission criterion failed: any")

    if "principal" in criterion:
        if principal is None:
            return custom or "submission criterion failed: principal unavailable"
        field = criterion["principal"]
        actual = getattr(principal, field, None)
        operator = criterion["operator"]
        expected = criterion["value"]
        try:
            passed = _OPERATORS[operator](actual, expected)
        except (TypeError, KeyError):
            passed = False
        if not passed:
            return custom or f"submission criterion failed: principal.{field} {operator} {expected!r} (actual: {actual!r})"
        return None

    property_name = criterion["property"]
    operator = criterion["operator"]
    expected = criterion["value"]
    actual = instance_row.get(property_name)
    try:
        passed = _OPERATORS[operator](actual, expected)
    except TypeError:
        passed = False
    if not passed:
        return custom or f"submission criterion failed: {property_name} {operator} {expected!r} (actual: {actual!r})"
    return None

def _deep_set(obj: dict, path: list[str], value: Any) -> dict:
    """Return a copy of ``obj`` with ``path`` set to ``value`` (nested dicts)."""
    root = dict(obj)
    cur = root
    for key in path[:-1]:
        nxt = cur.get(key)
        cur[key] = dict(nxt) if isinstance(nxt, dict) else {}
        cur = cur[key]
    cur[path[-1]] = value
    return root

