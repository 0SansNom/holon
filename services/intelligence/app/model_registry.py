"""Model Integration —
deliberately bounded, stated plainly up front rather than silently
narrowed later: this registers and serves already-trained model
artifacts (scikit-learn — no GPU story this environment can't support,
and no second inference runtime like ONNX for no added value at this
scale). It does **not** provide training infrastructure, experiment
tracking, or GPU orchestration — a model is trained *externally* (this
module has no `fit()` call anywhere) and registered here purely as an
artifact to serve, the same split Foundry's own docs draw between its
training/experimentation tooling and a model's deployed serving
endpoint.

Artifacts live in MinIO (the same S3-compatible object store the
Iceberg warehouse already uses — `models/` instead of `raw/`, a second
prefix in the *same* `holon-warehouse` bucket, not a new one) rather
than a Postgres column: a large binary blob doesn't belong in a
relational row, the same reasoning that already keeps Dataset content
in Iceberg instead of JSONB.

Deliberately no in-process artifact cache: every `predict` call
re-fetches and re-deserializes the artifact fresh, the same "no
explicit invalidation logic needed" trade-off `function_registry.py`
already makes for Function plugins — correctness (a re-registered model
takes effect on the very next call) over micro-optimizing a code path
this build's data volume never stresses.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any, Optional

import asyncpg
import joblib

logger = logging.getLogger("intelligence.model_registry")

DDL = """
CREATE TABLE IF NOT EXISTS model_registration (
    name TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    version TEXT NOT NULL,
    framework TEXT NOT NULL,
    artifact_key TEXT NOT NULL,
    input_schema JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_VALID_FRAMEWORKS = {"sklearn"}


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


def _artifact_key(name: str, version: str) -> str:
    return f"models/{name}/{version}/model.joblib"


def _parse_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result["input_schema"], str):
        result["input_schema"] = json.loads(result["input_schema"])
    return result


def _validate_artifact_sync(artifact_bytes: bytes, framework: str) -> None:
    """Real, synchronous validation, not just trusting the caller's
    bytes: a corrupt or incompatible artifact is rejected at
    registration time, not discovered on the first prediction request.
    Run through `asyncio.to_thread` by `register_model` — deserializing
    is CPU-bound, not I/O, so it still shouldn't block the event loop.
    """
    model = joblib.load(io.BytesIO(artifact_bytes))
    if not hasattr(model, "predict"):
        raise ValueError(f"deserialized {framework} artifact has no predict() method")


def _put_artifact_sync(s3_client, bucket: str, key: str, artifact_bytes: bytes) -> None:
    s3_client.put_object(Bucket=bucket, Key=key, Body=artifact_bytes)


async def register_model(
    pool: asyncpg.Pool,
    s3_client,
    bucket: str,
    *,
    tenant_id: str,
    name: str,
    version: str,
    framework: str,
    artifact_bytes: bytes,
    input_schema: dict,
) -> dict:
    if framework not in _VALID_FRAMEWORKS:
        raise ValueError(f"unknown framework {framework!r} (must be one of {sorted(_VALID_FRAMEWORKS)})")
    try:
        await asyncio.to_thread(_validate_artifact_sync, artifact_bytes, framework)
    except Exception as exc:
        raise ValueError(f"artifact does not deserialize as a valid {framework} model: {exc}") from exc

    key = _artifact_key(name, version)
    await asyncio.to_thread(_put_artifact_sync, s3_client, bucket, key, artifact_bytes)

    await pool.execute(
        """
        INSERT INTO model_registration (name, tenant_id, version, framework, artifact_key, input_schema, status)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'active')
        ON CONFLICT (name) DO UPDATE SET
            tenant_id = EXCLUDED.tenant_id, version = EXCLUDED.version, framework = EXCLUDED.framework,
            artifact_key = EXCLUDED.artifact_key, input_schema = EXCLUDED.input_schema, status = 'active'
        """,
        name, tenant_id, version, framework, key, json.dumps(input_schema),
    )
    return await get_model(pool, name)


async def get_model(pool: asyncpg.Pool, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM model_registration WHERE name = $1", name)
    return _parse_row(row) if row else None


async def list_models(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM model_registration WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [_parse_row(row) for row in rows]


async def set_model_status(pool: asyncpg.Pool, name: str, status: str) -> Optional[dict]:
    await pool.execute("UPDATE model_registration SET status = $1 WHERE name = $2", status, name)
    return await get_model(pool, name)


def _predict_sync(s3_client, bucket: str, artifact_key: str, properties: list[str], features: dict) -> Any:
    response = s3_client.get_object(Bucket=bucket, Key=artifact_key)
    artifact_bytes = response["Body"].read()
    model = joblib.load(io.BytesIO(artifact_bytes))
    feature_vector = [[features[p] for p in properties]]
    prediction = model.predict(feature_vector)[0]
    # numpy scalar types (e.g. numpy.int64) aren't JSON-serializable —
    # `.item()` converts to the equivalent plain Python type.
    return prediction.item() if hasattr(prediction, "item") else prediction


async def predict(pool: asyncpg.Pool, s3_client, bucket: str, *, name: str, features: dict) -> Any:
    registration = await get_model(pool, name)
    if registration is None:
        raise ValueError(f"no model registered as {name!r}")
    if registration["status"] != "active":
        raise ValueError(f"model {name!r} is {registration['status']}, not active")

    input_schema = registration["input_schema"]
    properties = list(input_schema.get("properties", {}).keys())
    required = input_schema.get("required", properties)
    missing = [f for f in required if f not in features]
    if missing:
        raise ValueError(f"missing required feature(s) for model {name!r}: {missing}")

    return await asyncio.to_thread(
        _predict_sync, s3_client, bucket, registration["artifact_key"], properties, features
    )
