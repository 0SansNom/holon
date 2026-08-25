"""Production security posture checks.

Call `assert_production_posture` at service lifespan start. No-op unless
`HOLON_ENV` is `prod` / `production`.
"""

from __future__ import annotations

import os


def is_production() -> bool:
    return os.environ.get("HOLON_ENV", "").strip().lower() in {"prod", "production"}


class ProductionSecurityError(RuntimeError):
    """Raised when HOLON_ENV=production and required posture flags are wrong."""


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def assert_production_posture(*, service_name: str) -> None:
    """No-op unless HOLON_ENV=production. Raises ProductionSecurityError with all violations."""
    if not is_production():
        return

    violations: list[str] = []
    name = service_name.lower()

    # Demo data is never auto-seeded. Empty-instance bootstrap is Identity's
    # ensure_instance_bootstrap only (admin + tenant/workspace). There is no
    # HOLON_ALLOW_DEV_LOGIN flag to check any more — bootstrap unconditionally
    # requires HOLON_BOOTSTRAP_ADMIN_SECRET in every environment (seed.py's
    # _require_bootstrap_admin_secret), so there's no permissive default left
    # for this check to guard against.

    if not (os.environ.get("HOLON_METRICS_TOKEN") or "").strip():
        violations.append("HOLON_METRICS_TOKEN must be set (non-empty) in production")

    cors = os.environ.get("HOLON_CORS_ORIGINS")
    if cors is None or not cors.strip():
        violations.append("HOLON_CORS_ORIGINS must be set in production")
    elif "localhost" in cors.lower() or "127.0.0.1" in cors:
        violations.append("HOLON_CORS_ORIGINS must not contain localhost or 127.0.0.1 in production")

    if "HOLON_MINTABLE_PRINCIPAL_URNS" not in os.environ:
        violations.append("HOLON_MINTABLE_PRINCIPAL_URNS must be set in production (empty string OK if service never mints SAs)")

    from .auth import jwt_algorithm

    if _truthy("HOLON_JWT_REQUIRE_ASYMMETRIC"):
        if jwt_algorithm() != "RS256":
            violations.append("HOLON_JWT_REQUIRE_ASYMMETRIC requires HOLON_JWT_ALG=RS256")
        if not (os.environ.get("HOLON_JWT_PUBLIC_KEYS") or os.environ.get("HOLON_JWT_PUBLIC_KEY") or "").strip():
            violations.append("HOLON_JWT_PUBLIC_KEYS (or HOLON_JWT_PUBLIC_KEY) must be set for RS256")
    elif jwt_algorithm() == "RS256":
        if not (os.environ.get("HOLON_JWT_PUBLIC_KEYS") or os.environ.get("HOLON_JWT_PUBLIC_KEY") or "").strip():
            violations.append("HOLON_JWT_PUBLIC_KEYS (or HOLON_JWT_PUBLIC_KEY) must be set when HOLON_JWT_ALG=RS256")

    if "identity" in name:
        if not _truthy("HOLON_ALLOW_USER_JWT_MINT"):
            violations.append("HOLON_ALLOW_USER_JWT_MINT must be truthy on Identity in production")
    elif _truthy("HOLON_ALLOW_USER_JWT_MINT"):
        violations.append(
            f"HOLON_ALLOW_USER_JWT_MINT must not be truthy on {service_name!r} in production (Identity only)"
        )

    if _truthy("HOLON_ALLOW_LOCAL_USER_MINT"):
        violations.append(
            "HOLON_ALLOW_LOCAL_USER_MINT must not be truthy in production "
            "(bypasses Identity-only user JWT minting)"
        )

    if "knowledge" in name and not _truthy("HOLON_SERVING_STORE_REQUIRE_MATERIALIZED"):
        violations.append(
            "HOLON_SERVING_STORE_REQUIRE_MATERIALIZED should be truthy on Knowledge in production"
        )

    if "intelligence" in name:
        if _truthy("HOLON_INTELLIGENCE_ENABLED"):
            violations.append(
                "HOLON_INTELLIGENCE_ENABLED must not be truthy in production "
                "(experimental — leave unset/false until opted in)"
            )
        if _truthy("HOLON_ALLOW_JOBLIB_MODELS"):
            violations.append(
                "HOLON_ALLOW_JOBLIB_MODELS must not be truthy in production "
                "(joblib/pickle deserialize is an RCE surface)"
            )
        if _truthy("HOLON_ALLOW_TOOL_PLUGIN_REGISTER"):
            violations.append(
                "HOLON_ALLOW_TOOL_PLUGIN_REGISTER must not be truthy in production "
                "(in-process plugin load is not a sandbox)"
            )

    if violations:
        raise ProductionSecurityError(
            f"production security posture failed for {service_name}: " + "; ".join(violations)
        )
