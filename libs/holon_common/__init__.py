from . import outbox
from . import event_catalog  # noqa: F401 — importing registers all payload schemas
from . import registry
from . import secrets as secrets_module
from . import audit as audit_module
from .resource import Classification, most_restrictive, union_markings
from .urn import URN, InvalidURNError, build as build_urn, parse as parse_urn
from .events import EventActor, EventConsumer, EventEnvelope, EventProducer
from .auth import (
    Principal,
    active_jwt,
    clear_session_cookie,
    decode_token,
    issue_token,
    load_jwt_secrets,
    make_principal_dependency,
    require_tenant_match,
    require_urn_tenant_match,
    set_session_cookie,
)
from .db import create_pool
from .errors import install_error_handlers
from .observability import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    configure_json_logging,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    retry_with_backoff,
)

__all__ = [
    "outbox",
    "event_catalog",
    "registry",
    "Classification",
    "most_restrictive",
    "union_markings",
    "URN",
    "InvalidURNError",
    "build_urn",
    "parse_urn",
    "EventActor",
    "EventConsumer",
    "EventEnvelope",
    "EventProducer",
    "Principal",
    "decode_token",
    "issue_token",
    "active_jwt",
    "load_jwt_secrets",
    "make_principal_dependency",
    "require_tenant_match",
    "require_urn_tenant_match",
    "set_session_cookie",
    "clear_session_cookie",
    "PermissionClient",
    "create_pool",
    "install_error_handlers",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "configure_json_logging",
    "instrument_cors",
    "instrument_metrics",
    "instrument_tracing",
    "retry_with_backoff",
    "secrets_module",
    "audit_module",
]


def __getattr__(name):
    # Lazy: PermissionClient needs httpx, which host-side white-box tests
    # (e.g. test_dlq.py) deliberately don't have installed — they only
    # ever need EventConsumer/create_pool/registry, so importing those
    # must not drag httpx in. Deferred here (PEP 562) rather than eagerly
    # at the top of this file, same reasoning `observability.py` already
    # applies to its own OTel imports.
    if name == "PermissionClient":
        from .authz import PermissionClient

        return PermissionClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
