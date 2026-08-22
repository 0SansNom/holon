"""Unit tests for the Iceberg destination configuration builder (no stack).

The one piece of `airbyte_registry.py` that's pure logic, not I/O —
covers the exact bug class the 2026-08-19 fix closed: an admin must
never be able to steer an Airbyte-backed source at a destination other
than Holon's own shared Iceberg REST catalog.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[3] / "services" / "connectivity" / "app"
_pkg = types.ModuleType("connectivity_app_test_pkg")
_pkg.__path__ = [str(_APP_DIR)]
sys.modules["connectivity_app_test_pkg"] = _pkg
_spec = importlib.util.spec_from_file_location(
    "connectivity_app_test_pkg.airbyte_registry", _APP_DIR / "airbyte_registry.py"
)
airbyte_registry = importlib.util.module_from_spec(_spec)
sys.modules["connectivity_app_test_pkg.airbyte_registry"] = airbyte_registry
_spec.loader.exec_module(airbyte_registry)

build_iceberg_destination_configuration = airbyte_registry.build_iceberg_destination_configuration


def test_builds_rest_catalog_destination_pointed_at_the_dataset_namespace() -> None:
    config = build_iceberg_destination_configuration(
        "airbyte_orders",
        catalog_uri="http://iceberg-rest:8181",
        warehouse="s3://holon-warehouse/",
        s3_endpoint="http://minio:9000",
        access_key="holon",
        secret_key="holon12345",
        region="us-east-1",
    )
    assert config == {
        "access_key_id": "holon",
        "secret_access_key": "holon12345",
        "s3_bucket_name": "holon-warehouse",
        "s3_bucket_region": "us-east-1",
        "s3_endpoint": "http://minio:9000",
        "warehouse_location": "s3://holon-warehouse/",
        "main_branch_name": "main",
        "catalog_type": {
            "catalog_type": "REST",
            "server_uri": "http://iceberg-rest:8181",
            "namespace": "airbyte_orders",
        },
    }


def test_bucket_name_extraction_handles_no_trailing_slash() -> None:
    config = build_iceberg_destination_configuration(
        "ds",
        catalog_uri="http://iceberg-rest:8181",
        warehouse="s3://holon-warehouse",
        s3_endpoint="http://minio:9000",
        access_key="holon",
        secret_key="secret",
        region="us-east-1",
    )
    assert config["s3_bucket_name"] == "holon-warehouse"


def test_register_airbyte_source_has_no_destination_configuration_parameter() -> None:
    """Regression guard for the actual fix: a caller must not be able to
    pass a destination_configuration at all, not just "shouldn't" by
    convention.
    """
    import inspect

    params = inspect.signature(airbyte_registry.register_airbyte_source).parameters
    assert "destination_configuration" not in params
    assert "iceberg_config" in params
