"""Live confidential-visibility decision.

One question, one clock: can this principal's country see confidential
property values? Asked of OPA through ``check_abac`` with
``classification=confidential``, cached five seconds per country.
OPA unreachable, or the circuit open, answers no — the caller masks.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from holon_common import Principal

logger = logging.getLogger("knowledge.policy")

_TTL_SECONDS = 5.0
_CONFIDENTIAL_RESOURCE = {"classification": "confidential"}

# country -> (visible, expires_at). ``None`` country is its own key.
_cache: dict[Optional[str], tuple[bool, float]] = {}
_authz = None


def _now() -> float:
    return time.monotonic()


def bind_authz(authz) -> None:
    """Lifespan hook. Tests bind a fake; production binds PermissionClient."""
    global _authz
    _authz = authz
    clear_visibility_cache()


def clear_visibility_cache() -> None:
    _cache.clear()


async def confidential_visible(principal: Principal) -> bool:
    """True when this country may read confidential property values."""
    country = principal.country
    now = _now()
    cached = _cache.get(country)
    if cached is not None and now < cached[1]:
        return cached[0]

    authz = _authz
    if authz is None:
        return False
    try:
        visible = bool(await authz.check_abac(principal, _CONFIDENTIAL_RESOURCE))
    except Exception:
        logger.warning(
            "confidential visibility check failed for country %s; masking",
            country,
            exc_info=True,
        )
        visible = False
    _cache[country] = (visible, now + _TTL_SECONDS)
    return visible
