"""Shared Holon API error contract.

Every service installs these handlers once. Clients always receive JSON:

```json
{
  "detail": "human-readable message",
  "errorCode": "NOT_FOUND",
  "errorName": "ObjectTypeNotFound",
  "errorInstanceId": "uuid",
  "parameters": { "object_type": "Customer" },
  "service": "knowledge-platform"
}
```

Prefer raising `HolonError` (or the `not_found` / `forbidden` / … helpers)
from new code. Legacy `HTTPException(detail="…")` is normalized into the
same envelope.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping, NoReturn, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("holon_common.errors")

# Stable machine codes (HTTP-semantic classes, not per-endpoint names).
CODE_UNAUTHORIZED = "unauthorized"
CODE_FORBIDDEN = "forbidden"
CODE_NOT_FOUND = "not_found"
CODE_INVALID_ARGUMENT = "invalid_argument"
CODE_CONFLICT = "conflict"
CODE_RATE_LIMITED = "rate_limited"
CODE_UNAVAILABLE = "unavailable"
CODE_INTERNAL = "internal"

_STATUS_TO_CODE: dict[int, str] = {
    400: CODE_INVALID_ARGUMENT,
    401: CODE_UNAUTHORIZED,
    403: CODE_FORBIDDEN,
    404: CODE_NOT_FOUND,
    409: CODE_CONFLICT,
    415: CODE_INVALID_ARGUMENT,
    422: CODE_INVALID_ARGUMENT,
    429: CODE_RATE_LIMITED,
    503: CODE_UNAVAILABLE,
    500: CODE_INTERNAL,
}


def code_for_status(status_code: int) -> str:
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code == 502:
        return CODE_UNAVAILABLE
    if 400 <= status_code < 500:
        return CODE_INVALID_ARGUMENT
    if status_code == 503:
        return CODE_UNAVAILABLE
    return CODE_INTERNAL


def new_instance_id() -> str:
    return str(uuid.uuid4())


# Wire-facing UPPER codes for the public error envelope.
_WIRE_ERROR_CODE: dict[str, str] = {
    CODE_UNAUTHORIZED: "UNAUTHORIZED",
    CODE_FORBIDDEN: "PERMISSION_DENIED",
    CODE_NOT_FOUND: "NOT_FOUND",
    CODE_INVALID_ARGUMENT: "INVALID_ARGUMENT",
    CODE_CONFLICT: "CONFLICT",
    CODE_RATE_LIMITED: "RATE_LIMITED",
    CODE_UNAVAILABLE: "UNAVAILABLE",
    CODE_INTERNAL: "INTERNAL",
}


def wire_error_code(error_code: str) -> str:
    return _WIRE_ERROR_CODE.get(error_code, error_code.upper())


def error_body(
    *,
    detail: str,
    error_code: str,
    error_name: str,
    service: str,
    parameters: Optional[Mapping[str, Any]] = None,
    error_instance_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build the canonical public error JSON object."""
    instance_id = error_instance_id or new_instance_id()
    return {
        "detail": detail,
        "errorCode": wire_error_code(error_code),
        "errorName": error_name,
        "errorInstanceId": instance_id,
        "parameters": dict(parameters or {}),
        "service": service,
    }


class HolonError(Exception):
    """Typed application error — prefer this over bare HTTPException."""

    def __init__(
        self,
        status_code: int,
        error_name: str,
        detail: str,
        *,
        error_code: Optional[str] = None,
        parameters: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)
        self.error_name = error_name
        self.detail = detail
        self.error_code = error_code or code_for_status(self.status_code)
        self.parameters = dict(parameters or {})
        self.headers = dict(headers or {})

    def to_body(self, *, service: str, error_instance_id: Optional[str] = None) -> dict[str, Any]:
        return error_body(
            detail=self.detail,
            error_code=self.error_code,
            error_name=self.error_name,
            service=service,
            parameters=self.parameters,
            error_instance_id=error_instance_id,
        )

    # ---- factories -------------------------------------------------------

    @classmethod
    def from_http(
        cls,
        status_code: int,
        detail: str,
        *,
        error_name: Optional[str] = None,
        parameters: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> HolonError:
        """Build from an HTTP status when a more specific factory does not fit."""
        name = error_name or {
            400: "InvalidRequest",
            401: "Unauthorized",
            403: "Forbidden",
            404: "NotFound",
            409: "Conflict",
            422: "ValidationFailed",
            429: "RateLimited",
            502: "BadGateway",
            503: "Unavailable",
            500: "InternalError",
        }.get(int(status_code), "RequestFailed")
        return cls(
            status_code,
            name,
            detail,
            error_code=code_for_status(int(status_code)),
            parameters=parameters,
            headers=headers,
        )

    @classmethod
    def invalid_argument(cls, error_name: str, detail: str, **parameters: Any) -> HolonError:
        return cls(400, error_name, detail, error_code=CODE_INVALID_ARGUMENT, parameters=parameters)

    @classmethod
    def unauthorized(cls, error_name: str, detail: str, **parameters: Any) -> HolonError:
        return cls(401, error_name, detail, error_code=CODE_UNAUTHORIZED, parameters=parameters)

    @classmethod
    def forbidden(cls, error_name: str, detail: str, **parameters: Any) -> HolonError:
        return cls(403, error_name, detail, error_code=CODE_FORBIDDEN, parameters=parameters)

    @classmethod
    def not_found(cls, error_name: str, detail: str, **parameters: Any) -> HolonError:
        return cls(404, error_name, detail, error_code=CODE_NOT_FOUND, parameters=parameters)

    @classmethod
    def conflict(cls, error_name: str, detail: str, **parameters: Any) -> HolonError:
        return cls(409, error_name, detail, error_code=CODE_CONFLICT, parameters=parameters)

    @classmethod
    def rate_limited(cls, error_name: str, detail: str, **parameters: Any) -> HolonError:
        return cls(429, error_name, detail, error_code=CODE_RATE_LIMITED, parameters=parameters)

    @classmethod
    def unavailable(cls, error_name: str, detail: str, **parameters: Any) -> HolonError:
        return cls(503, error_name, detail, error_code=CODE_UNAVAILABLE, parameters=parameters)

    @classmethod
    def internal(cls, error_name: str, detail: str, **parameters: Any) -> HolonError:
        return cls(500, error_name, detail, error_code=CODE_INTERNAL, parameters=parameters)


def raise_invalid_argument(error_name: str, detail: str, **parameters: Any) -> NoReturn:
    raise HolonError.invalid_argument(error_name, detail, **parameters)


def raise_unauthorized(error_name: str, detail: str, **parameters: Any) -> NoReturn:
    raise HolonError.unauthorized(error_name, detail, **parameters)


def raise_forbidden(error_name: str, detail: str, **parameters: Any) -> NoReturn:
    raise HolonError.forbidden(error_name, detail, **parameters)


def raise_not_found(error_name: str, detail: str, **parameters: Any) -> NoReturn:
    raise HolonError.not_found(error_name, detail, **parameters)


def raise_conflict(error_name: str, detail: str, **parameters: Any) -> NoReturn:
    raise HolonError.conflict(error_name, detail, **parameters)


def raise_rate_limited(error_name: str, detail: str, **parameters: Any) -> NoReturn:
    raise HolonError.rate_limited(error_name, detail, **parameters)


def raise_unavailable(error_name: str, detail: str, **parameters: Any) -> NoReturn:
    raise HolonError.unavailable(error_name, detail, **parameters)


def _detail_message(detail: Any) -> str:
    if detail is None:
        return "request failed"
    if isinstance(detail, str):
        return detail
    if isinstance(detail, (list, dict)):
        # FastAPI validation / rare structured HTTPException details.
        return "request failed"
    return str(detail)


def _legacy_http_name(status_code: int) -> str:
    return {
        400: "InvalidRequest",
        401: "Unauthorized",
        403: "Forbidden",
        404: "NotFound",
        409: "Conflict",
        422: "ValidationFailed",
        429: "RateLimited",
        503: "Unavailable",
        500: "InternalError",
    }.get(status_code, "RequestFailed")


def _log_error(
    *,
    service: str,
    request: Request,
    status_code: int,
    error_name: str,
    error_code: str,
    error_instance_id: str,
    detail: str,
    exc: Optional[Exception] = None,
) -> None:
    extra = {
        "error_instance_id": error_instance_id,
        "error_name": error_name,
        "error_code": error_code,
        "status_code": status_code,
    }
    msg = "%s %s %s → %s %s (%s) id=%s detail=%s"
    args = (
        service,
        request.method,
        request.url.path,
        status_code,
        error_name,
        error_code,
        error_instance_id,
        detail,
    )
    if status_code >= 500:
        logger.error(msg, *args, extra=extra, exc_info=exc is not None)
    else:
        logger.warning(msg, *args, extra=extra)


def install_error_handlers(app: FastAPI, *, service_name: str) -> None:
    """Install Holon error handlers. Call once per service at import/boot."""

    @app.exception_handler(HolonError)
    async def _holon_error_handler(request: Request, exc: HolonError) -> JSONResponse:
        instance_id = new_instance_id()
        body = exc.to_body(service=service_name, error_instance_id=instance_id)
        _log_error(
            service=service_name,
            request=request,
            status_code=exc.status_code,
            error_name=exc.error_name,
            error_code=exc.error_code,
            error_instance_id=instance_id,
            detail=exc.detail,
        )
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers or None)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        instance_id = new_instance_id()
        detail = "request validation failed"
        body = error_body(
            detail=detail,
            error_code=CODE_INVALID_ARGUMENT,
            error_name="RequestValidationFailed",
            service=service_name,
            parameters={"errors": exc.errors()},
            error_instance_id=instance_id,
        )
        _log_error(
            service=service_name,
            request=request,
            status_code=422,
            error_name="RequestValidationFailed",
            error_code=CODE_INVALID_ARGUMENT,
            error_instance_id=instance_id,
            detail=detail,
        )
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        instance_id = new_instance_id()
        # Structured detail from call sites that already pass a dict envelope.
        if isinstance(exc.detail, dict) and "error_name" in exc.detail and "detail" in exc.detail:
            raw = exc.detail
            message = str(raw.get("detail") or "request failed")
            error_name = str(raw.get("error_name") or _legacy_http_name(exc.status_code))
            error_code = str(raw.get("error_code") or code_for_status(exc.status_code))
            parameters = raw.get("parameters") if isinstance(raw.get("parameters"), dict) else {}
        else:
            message = _detail_message(exc.detail)
            error_name = _legacy_http_name(exc.status_code)
            error_code = code_for_status(exc.status_code)
            parameters = {}
        body = error_body(
            detail=message,
            error_code=error_code,
            error_name=error_name,
            service=service_name,
            parameters=parameters,
            error_instance_id=instance_id,
        )
        _log_error(
            service=service_name,
            request=request,
            status_code=exc.status_code,
            error_name=error_name,
            error_code=error_code,
            error_instance_id=instance_id,
            detail=message,
        )
        headers = getattr(exc, "headers", None) or None
        return JSONResponse(status_code=exc.status_code, content=body, headers=headers)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        instance_id = new_instance_id()
        detail = "internal server error"
        body = error_body(
            detail=detail,
            error_code=CODE_INTERNAL,
            error_name="InternalError",
            service=service_name,
            error_instance_id=instance_id,
        )
        _log_error(
            service=service_name,
            request=request,
            status_code=500,
            error_name="InternalError",
            error_code=CODE_INTERNAL,
            error_instance_id=instance_id,
            detail=detail,
            exc=exc,
        )
        return JSONResponse(status_code=500, content=body)
