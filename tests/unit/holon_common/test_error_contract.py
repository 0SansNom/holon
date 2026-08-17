"""Tests for Error Contract."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))

from holon_common.errors import HolonError, install_error_handlers  # noqa: E402


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app, service_name="test-service")

    @app.get("/typed")
    async def typed() -> None:
        raise HolonError.not_found("ObjectTypeNotFound", "unknown ObjectType: X", object_type="X")

    @app.get("/legacy")
    async def legacy() -> None:
        raise HTTPException(status_code=404, detail="legacy missing")

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    @app.get("/validate")
    async def validate(q: int) -> dict:
        return {"q": q}

    return app


def test_holon_error_envelope() -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    response = client.get("/typed")
    assert response.status_code == 404
    body = response.json()
    assert body["detail"] == "unknown ObjectType: X"
    assert body["errorCode"] == "NOT_FOUND"
    assert body["errorName"] == "ObjectTypeNotFound"
    assert body["parameters"] == {"object_type": "X"}
    assert body["service"] == "test-service"
    assert body["errorInstanceId"]


def test_legacy_http_exception_normalized() -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    response = client.get("/legacy")
    assert response.status_code == 404
    body = response.json()
    assert body["detail"] == "legacy missing"
    assert body["errorCode"] == "NOT_FOUND"
    assert body["errorName"] == "NotFound"
    assert body["service"] == "test-service"


def test_unhandled_is_safe_500() -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "internal server error"
    assert body["errorCode"] == "INTERNAL"
    assert body["errorName"] == "InternalError"
    assert "secret" not in body["detail"]


def test_validation_error_envelope() -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    response = client.get("/validate")
    assert response.status_code == 422
    body = response.json()
    assert body["errorCode"] == "INVALID_ARGUMENT"
    assert body["errorName"] == "RequestValidationFailed"
    assert body["detail"] == "request validation failed"
    assert "errors" in body["parameters"]
