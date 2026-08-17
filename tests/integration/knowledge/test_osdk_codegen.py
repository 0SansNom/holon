"""Tests for Osdk Codegen."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import CONNECTIVITY, KNOWLEDGE, _request, _unique_name, ontology_url, holon_url

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_osdk import emit_python, emit_typescript, fetch_schema  # noqa: E402

REVIEWS_WITH_TAGS_API = "http://reviews-api:8000/reviews_with_tags.json"

TSC_PATH = Path(__file__).resolve().parents[3] / "services/experience/web/node_modules/.bin/tsc"


@pytest.fixture(scope="module")
def sample_ontology(msmith_token: str, jdoe_token: str) -> dict:
    """A real self-serve ObjectType with a struct property, a Value."""
    source_name = _unique_name("osdk_reviews")
    status, registration = _request(
        "POST", f"{CONNECTIVITY}/sources", token=jdoe_token,
        body={"name": source_name, "base_url": REVIEWS_WITH_TAGS_API},
    )
    assert status == 200, registration
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result

    object_type_name = _unique_name("OsdkReview")
    status, object_type = _request(
        "POST", holon_url("/object-types"), token=msmith_token,
        body={
            "name": object_type_name, "source_dataset_urn": result["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment", "address": "address_json"},
            "description": "pytest-created",
        },
    )
    assert status == 201, object_type

    value_type_name = _unique_name("OsdkCity")
    status, _ = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token,
        body={"name": value_type_name, "base_type": "string"},
    )
    assert status == 201

    comment_value_type_name = _unique_name("OsdkCommentType")
    status, _ = _request(
        "POST", ontology_url("/valueTypes"), token=msmith_token,
        body={"name": comment_value_type_name, "base_type": "string"},
    )
    assert status == 201

    shared_property_type_name = _unique_name("osdk_review_comment")
    status, _ = _request(
        "POST", ontology_url("/sharedPropertyTypes"), token=msmith_token,
        body={
            "api_name": shared_property_type_name, "display_name": "Review comment",
            "value_type": comment_value_type_name, "description": "Free-text reviewer comment",
        },
    )
    assert status == 201

    status, draft = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions"), token=msmith_token,
        body={"property_types": {
            "address": {"kind": "struct", "properties": {"city": {"kind": "value_type", "value_type": value_type_name}}},
            "comment": {"kind": "shared_property_type", "shared_property_type": shared_property_type_name},
        }},
    )
    assert status == 201, draft
    status, published = _request(
        "POST", ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, published

    action_name = f"{object_type_name}.setPriority"
    status, action_type = _request(
        "POST", ontology_url("/actionTypes"), token=msmith_token,
        body={
            "name": action_name, "target_object_type": object_type_name, "required_permission": "write",
            "risk_level": "low", "description": "sets a review's priority",
            "parameters": [{"name": "priority", "value_type": value_type_name, "required": True}],
            "edits": [{"property": "priorityLevel", "source": "parameter", "parameter_name": "priority"}],
        },
    )
    assert status == 201, action_type

    interface_name = _unique_name("OsdkHasComment")
    status, _ = _request(
        "POST", ontology_url("/interfaceTypes"), token=msmith_token,
        body={"name": interface_name, "required_properties": ["comment"], "description": "osdk iface"},
    )
    assert status == 201

    return {
        "object_type_name": object_type_name, "action_name": action_name, "value_type_name": value_type_name,
        "shared_property_type_name": shared_property_type_name,
        "interface_name": interface_name,
    }


def test_generated_python_contains_the_typed_object_and_action(sample_ontology: dict, jdoe_token: str, tmp_path: Path) -> None:
    schema = fetch_schema(knowledge_url=KNOWLEDGE, token=jdoe_token)
    output = emit_python(schema)

    object_type_name = sample_ontology["object_type_name"]
    assert f"class {object_type_name}:" in output, output
    assert f"class {object_type_name}_Address:" in output, output
    assert "city: str" in output, output
    assert f"def {object_type_name}_setPriority(" in output, output
    assert "priority: Any" in output, output
    assert f'/actions/{sample_ontology["action_name"]}"' in output, output
    assert "comment: Optional[str] = None  # Review comment — Free-text reviewer comment" in output, output
    interface_name = sample_ontology["interface_name"]
    assert f"class {interface_name}:" in output, output
    assert f"def list_interface_{interface_name}(" in output, output
    assert f'/api/ontologies/main/interfaceTypes/{interface_name}/objects"' in output, output

    module_path = tmp_path / "holon_ontology_test_output.py"
    module_path.write_text(output)
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(tmp_path)!r}); sys.path.insert(0, {str(Path(__file__).resolve().parents[3] / 'libs')!r}); import holon_ontology_test_output"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_generated_typescript_contains_the_typed_object_and_action_and_type_checks(sample_ontology: dict, jdoe_token: str, tmp_path: Path) -> None:
    schema = fetch_schema(knowledge_url=KNOWLEDGE, token=jdoe_token)
    output = emit_typescript(schema)

    object_type_name = sample_ontology["object_type_name"]
    assert f"export interface {object_type_name} {{" in output, output
    assert f"export interface {object_type_name}_Address {{" in output, output
    assert "city: string;" in output, output
    assert f"export async function {object_type_name}_setPriority(" in output, output
    assert "priority: unknown" in output, output
    assert "/** Review comment — Free-text reviewer comment */" in output, output
    assert "comment?: string;" in output, output
    interface_name = sample_ontology["interface_name"]
    assert f"export interface {interface_name} {{" in output, output
    assert f"export async function list_interface_{interface_name}(" in output, output
    assert f"`${{knowledgeUrl}}/api/ontologies/main/interfaceTypes/{interface_name}/objects`" in output, output

    if not TSC_PATH.exists():
        pytest.skip("tsc not installed under services/experience/web/node_modules — run `npm install` there first")

    ts_path = tmp_path / "holon_ontology_test_output.ts"
    ts_path.write_text(output)
    result = subprocess.run(
        [str(TSC_PATH), "--noEmit", "--target", "es2020", "--module", "esnext", "--moduleResolution", "bundler",
         "--strict", "--lib", "es2020,dom", str(ts_path)],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_putoncredithold_uses_the_one_generic_action_route(jdoe_token: str) -> None:
    """Every Action Type is declarative now (`putOnCreditHold`/`closeAccount`."""
    schema = fetch_schema(knowledge_url=KNOWLEDGE, token=jdoe_token)
    output = emit_python(schema)
    assert 'f"{knowledge_url}/api/ontologies/main/objects/Customer/{instance_id}/actions/Customer.putOnCreditHold"' in output, output
    assert '"reason": reason, "parameters"' in output, output


def test_generated_python_emits_relation_type_link_accessors(jdoe_token: str) -> None:
    schema = fetch_schema(knowledge_url=KNOWLEDGE, token=jdoe_token)
    output = emit_python(schema)
    assert "def get_Order_customer(" in output, output
    assert "def get_Customer_orders(" in output, output
    assert "def link_Order_customer(" in output, output
    assert "def unlink_Order_customer(" in output, output
    assert '/objects/Order/{instance_id}/links/customer"' in output, output

    ts = emit_typescript(schema)
    assert "export async function get_Order_customer(" in ts, ts
    assert "export async function link_Order_customer(" in ts, ts
    assert "export async function unlink_Order_customer(" in ts, ts
