"""ML model registry and predict."""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from holon_common import HolonError, Principal

from .. import deps, model_registry
from ..deps import (
    MODEL_BUCKET,
    _authorize_ml_model,
    _authorize_workspace,
    _filter_readable,
    _seed_ml_model_authz,
    current_principal,
    ml_model_urn,
    model_not_found,
    require_intelligence_enabled,
)

router = APIRouter()

class RegisterModelRequest(BaseModel):
    version: str
    framework: str = "sklearn"
    artifact_base64: str
    input_schema: dict


@router.post("/models/{name}")
async def register_model(
    name: str, body: RegisterModelRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Register an already-trained model artifact."""
    require_intelligence_enabled()
    existing = await model_registry.get_model(deps.pool, name)
    if existing is None:
        await _authorize_workspace(principal, "write")
    else:
        await _authorize_ml_model(principal, "write", name=name)
    try:
        artifact_bytes = base64.b64decode(body.artifact_base64)
    except Exception as exc:
        raise HolonError.invalid_argument('InvalidBase64Artifact', f"artifact_base64 is not valid base64: {exc}") from exc
    try:
        registration = await model_registry.register_model(
            deps.pool,
            deps.s3,
            MODEL_BUCKET,
            tenant_id=principal.tenant_id,
            name=name,
            version=body.version,
            framework=body.framework,
            artifact_bytes=artifact_bytes,
            input_schema=body.input_schema,
        )
    except model_registry.ModelRegistryError as exc:
        raise HolonError.from_http(exc.http_status, str(exc), error_name="ModelRegistryError") from exc
    except ValueError as exc:
        raise HolonError.invalid_argument("ModelRegistryError", str(exc)) from exc

    if existing is None:

        async def _compensate():
            await deps.pool.execute("DELETE FROM model_registration WHERE name = $1", name)

        await _seed_ml_model_authz(
            tenant_id=principal.tenant_id, name=name, compensate_delete=_compensate
        )
    return registration



@router.get("/models")
async def list_models(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    rows = await model_registry.list_models(deps.pool, principal.tenant_id)
    return await _filter_readable(
        principal,
        "ml_model",
        rows,
        urn_fn=lambda row: ml_model_urn(principal.tenant_id, row["name"]),
    )


@router.get("/models/{name}")
async def get_model(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await model_registry.get_model(deps.pool, name)
    if registration is None:
        raise model_not_found(name)
    await _authorize_ml_model(principal, "read", name=name)
    return registration


@router.post("/models/{name}/disable")
async def disable_model(name: str, principal: Principal = Depends(current_principal)) -> dict:
    if await model_registry.get_model(deps.pool, name) is None:
        raise model_not_found(name)
    await _authorize_ml_model(principal, "write", name=name)
    return await model_registry.set_model_status(deps.pool, name, "disabled")


@router.post("/models/{name}/enable")
async def enable_model(name: str, principal: Principal = Depends(current_principal)) -> dict:
    if await model_registry.get_model(deps.pool, name) is None:
        raise model_not_found(name)
    await _authorize_ml_model(principal, "write", name=name)
    return await model_registry.set_model_status(deps.pool, name, "active")


class PredictRequest(BaseModel):
    features: dict


@router.post("/models/{name}/predict")
async def predict(name: str, body: PredictRequest, principal: Principal = Depends(current_principal)) -> dict:
    """Execute model prediction inference synchronously."""
    if await model_registry.get_model(deps.pool, name) is None:
        raise model_not_found(name)
    await _authorize_ml_model(principal, "read", name=name)
    try:
        prediction = await model_registry.predict(
            deps.pool, deps.s3, MODEL_BUCKET, name=name, features=body.features
        )
    except model_registry.ModelRegistryError as exc:
        raise HolonError.from_http(exc.http_status, str(exc), error_name="ModelRegistryError") from exc
    except ValueError as exc:
        raise HolonError.invalid_argument("ModelRegistryError", str(exc)) from exc
    return {"model": name, "prediction": prediction}
