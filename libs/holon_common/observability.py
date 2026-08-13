"""Shared observability & resilience primitives (metrics,
traces, structured logs, retry with backoff+jitter, circuit breaker,
explicit timeouts, health probes).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import secrets as secrets_mod
import sys
import time
from typing import Any, Awaitable, Callable, Optional, Tuple, Type

from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger("holon_common.observability")


# ---- Structured JSON logs -------------------------------------------


class _JSONFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self._service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_json_logging(service_name: str, *, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter(service_name))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


# ---- Prometheus metrics ---------------------------------------------

_REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["service", "method", "path", "status"]
)
_REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency in seconds", ["service", "method", "path"]
)


class _MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, service_name: str) -> None:
        super().__init__(app)
        self._service_name = service_name

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start
        route = request.scope.get("route")
        path = route.path if route is not None else request.url.path  # templated path, not raw (avoids per-id cardinality)
        _REQUEST_COUNT.labels(self._service_name, request.method, path, response.status_code).inc()
        _REQUEST_LATENCY.labels(self._service_name, request.method, path).observe(duration)
        return response


def instrument_cors(app: FastAPI) -> None:
    """Enables CORS middleware for the React SPA. `allow_credentials=True`
    is required for the browser to send/receive the `holon_session`
    HttpOnly cookie cross-origin (the SPA's own origin and this service
    are different ports) — browsers reject a wildcard `allow_origins`
    once credentials are involved, so this is a concrete list instead,
    covering both ways this repo is actually run today: `npm run dev`
    (`:5173`) and the docker-compose profile, where Experience serves
    the built SPA itself (`:8004`). Override via `HOLON_CORS_ORIGINS`
    (comma-separated) for any other deployment.
    """
    from starlette.middleware.cors import CORSMiddleware

    origins = os.environ.get("HOLON_CORS_ORIGINS", "http://localhost:5173,http://localhost:8004").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def instrument_metrics(app: FastAPI, *, service_name: str) -> None:
    """Adds request count/latency instrumentation to every route and
    mounts `GET /metrics` in Prometheus text format.

    When `HOLON_METRICS_TOKEN` is set, scrapers must send
    `Authorization: Bearer <token>`. Leave unset only on trusted networks
    (local compose); production Helm must set it.
    """
    app.add_middleware(_MetricsMiddleware, service_name=service_name)
    metrics_token = (os.environ.get("HOLON_METRICS_TOKEN") or "").strip() or None
    if metrics_token is None:
        logger.warning(
            "%s: HOLON_METRICS_TOKEN unset — /metrics is unauthenticated (dev only)",
            service_name,
        )

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        if metrics_token is not None:
            auth = request.headers.get("authorization") or ""
            expected = f"Bearer {metrics_token}"
            if not secrets_mod.compare_digest(auth, expected):
                from fastapi import HTTPException

                raise HTTPException(status_code=401, detail="metrics unauthorized")
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---- Retry with exponential backoff + jitter ------------------------


async def retry_with_backoff(
    fn: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 15,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    retry_on: Tuple[Type[BaseException], ...] = (Exception,),
    what: str = "operation",
) -> Any:
    """Exponential backoff (`base_delay * 2**attempt`, capped at
    `max_delay`) with optional jitter (±50%) to avoid synchronized retry
    storms across replicas.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except retry_on as exc:
            if attempt == attempts:
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay *= 0.5 + random.random()
            logger.warning("%s: attempt %d/%d failed (%s), retrying in %.1fs", what, attempt, attempts, exc, delay)
            await asyncio.sleep(delay)


# ---- Circuit breaker -------------------------------------------------


class CircuitBreakerOpenError(Exception):
    pass


class CircuitBreaker:
    """Closed / open / half-open, hand-rolled rather than a new
    dependency (this build's entire outbound-call surface is small enough
    that a ~40-line state machine covers it). Opens after
    `failure_threshold` consecutive failures; while open, calls are
    rejected immediately (no wasted round-trip to an already-struggling
    dependency); after `cooldown_seconds`, one trial call is let through
    (half-open) — success closes it again, failure re-opens it.
    """

    def __init__(self, *, name: str, failure_threshold: int = 5, cooldown_seconds: float = 30.0) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._failures = 0
        self._state = "closed"
        self._opened_at: Optional[float] = None

    async def call(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        if self._state == "open":
            if time.monotonic() - self._opened_at < self._cooldown_seconds:
                raise CircuitBreakerOpenError(f"circuit '{self._name}' is open")
            self._state = "half_open"

        try:
            result = await fn()
        except Exception:
            self._failures += 1
            if self._state == "half_open" or self._failures >= self._failure_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()
                logger.warning("circuit '%s' opened after %d consecutive failures", self._name, self._failures)
            raise
        else:
            self._failures = 0
            self._state = "closed"
            return result


# ---- OpenTelemetry traces --------------------------------------------


def instrument_tracing(app: FastAPI, *, service_name: str, otlp_endpoint: str) -> None:
    """Automatic spans on every inbound request, outbound `httpx` call, and
    Postgres query — no manual span code needed per service beyond this
    one call. Imports are local to this function (not at module level):
    `observability.py` is imported by every service, including through
    `holon_common`'s package `__init__`, and host-side test tooling that
    only needs `EventConsumer`/`create_pool` (e.g. `tests/test_dlq.py`)
    has no reason to also require the OpenTelemetry SDK installed.

    Empty / unset `otlp_endpoint` disables tracing (no exporter, no
    connection-refused spam). Compose defaults to off; point
    `HOLON_OTLP_ENDPOINT` at a real collector in prod.
    """
    endpoint = (otlp_endpoint or "").strip()
    if not endpoint:
        logger.info("%s: HOLON_OTLP_ENDPOINT unset — tracing disabled", service_name)
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    AsyncPGInstrumentor().instrument()  # a no-op for services with no Postgres pool (e.g. Experience) — harmless
