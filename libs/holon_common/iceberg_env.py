"""Iceberg catalog connection settings from process environment.

Connectivity (writers) and Knowledge (readers) share the same env contract.
Table naming stays in `iceberg_ident`.
"""

from __future__ import annotations

import os


def iceberg_catalog_config_from_env() -> dict:
    """Kwargs for opening the warehouse catalog (no tenant_id)."""
    return dict(
        catalog_uri=os.environ["HOLON_ICEBERG_CATALOG_URI"],
        warehouse=os.environ["HOLON_ICEBERG_WAREHOUSE"],
        s3_endpoint=os.environ["HOLON_S3_ENDPOINT"],
        access_key=os.environ["AWS_ACCESS_KEY_ID"],
        secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region=os.environ["AWS_REGION"],
    )


def iceberg_kwargs(tenant_id: str, *, config: dict | None = None) -> dict:
    """Catalog kwargs scoped to a tenant (for reads/writes)."""
    base = config if config is not None else iceberg_catalog_config_from_env()
    return {**base, "tenant_id": tenant_id}
