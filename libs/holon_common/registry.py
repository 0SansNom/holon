"""Event payload schema registry.

Every `event_type` published on the Platform Event Bus MUST be registered
here with a versioned payload schema before any service emits it.
Both `EventProducer.publish` and `EventConsumer` validate payloads against this
registry.

Schemas live in `holon_common.event_catalog`.
"""

from __future__ import annotations

from typing import Any, Callable, Type

from pydantic import BaseModel


class UnknownEventTypeError(KeyError):
    """Raised when an event_type has no registered payload schema at all."""


class UnknownSchemaVersionError(KeyError):
    """Raised when an event_type is registered but not at the requested version."""


_REGISTRY: dict[tuple[str, int], Type[BaseModel]] = {}


def register(event_type: str, version: int = 1) -> Callable[[Type[BaseModel]], Type[BaseModel]]:
    """Registers a pydantic model as the payload schema of `event_type`.

    A new version is registered alongside the old one — never edited in
    place — so consumers lagging behind keep validating (R-B.2).
    """

    def decorator(model: Type[BaseModel]) -> Type[BaseModel]:
        key = (event_type, version)
        if key in _REGISTRY:
            raise ValueError(f"duplicate registration for {event_type} v{version}")
        _REGISTRY[key] = model
        return model

    return decorator


def validate(event_type: str, schema_version: int, payload: dict[str, Any]) -> BaseModel:
    """Validates a raw payload against the registered schema. Raises
    UnknownEventTypeError / UnknownSchemaVersionError / pydantic.ValidationError.
    """
    versions = [v for (t, v) in _REGISTRY if t == event_type]
    if not versions:
        raise UnknownEventTypeError(
            f"no payload schema registered for {event_type!r} — add it to holon_common.event_catalog"
        )
    model = _REGISTRY.get((event_type, schema_version))
    if model is None:
        raise UnknownSchemaVersionError(
            f"{event_type!r} is registered for version(s) {sorted(versions)}, not v{schema_version}"
        )
    return model.model_validate(payload)


def registered_event_types() -> list[tuple[str, int]]:
    """The (event_type, version) pairs currently registered — for tests and docs."""
    return sorted(_REGISTRY)
