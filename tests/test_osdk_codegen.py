"""The OSDK generator — typed client code generation:
`libs/holon_osdk/` walks the live
ontology (`schema.py`) and renders typed Python (`emit_python.py`) or
TypeScript (`emit_typescript.py`) — one `@dataclass`/`interface` per
ObjectType (with a real nested class/interface for a `struct` property,
`list`/`Array` for `array` — the one level of nesting the ontology
itself allows, `_validate_property_types` already enforces no more),
one typed function per Action Type.

Registers a real Value Type + a struct/array-typed self-serve
ObjectType + a declarative Action Type, generates against the live
stack, and checks the output textually for the right names ("golden
output," not a fragile character-for-character comparison) — then goes
further than that: actually imports the generated Python module and
actually type-checks the generated TypeScript with `tsc --noEmit`, so a
generator regression that produces syntactically-plausible-but-broken
output would be caught here, not just a string match that happens to
still be present.

Covers three generator-specific constraints: (1) `GET /actions/{name}`
must expose a declarative Action's `parameters` publicly (only via an
internal `_declarative` field `get_action` otherwise strips) — no
caller, generator or otherwise, could discover what to pass without it;
see `actions._get_action_definition`. (2) the two hardcoded Actions are
only reachable at `/objects/Customer/{id}/actions/{local_name}` (their
own specific route) while a declarative Action Type needs the *full*
dotted name against the generic route — the generator must pick the
right one per Action. (3) two different ObjectTypes can declare an
action with the same local name (e.g. "archive") — an unnamespaced
function would silently overwrite the earlier one in Python and fail to
compile at all in TypeScript; declarative functions are namespaced by
`target_object_type` to keep every name unique. Requires the stack running
(`make up`) and `tsc` available under `services/experience/web/
node_modules` (already installed for the frontend build). No real LLM
calls.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import CONNECTIVITY, KNOWLEDGE, _request, _unique_name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))

from holon_osdk import emit_python, emit_typescript, fetch_schema  # noqa: E402

REVIEWS_WITH_TAGS_API = "http://reviews-api:8000/reviews_with_tags.json"

TSC_PATH = Path(__file__).resolve().parent.parent / "services/experience/web/node_modules/.bin/tsc"


@pytest.fixture(scope="module")
def sample_ontology(msmith_token: str, jdoe_token: str) -> dict:
    """A real self-serve ObjectType with a struct property, a Value
    Type, and a declarative Action Type referencing it — enough surface
    to exercise every code path both emitters have (typed leaf, nested
    struct, Action parameters).
    """
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
        "POST", f"{KNOWLEDGE}/object-types", token=msmith_token,
        body={
            "name": object_type_name, "source_dataset_urn": result["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment", "address": "address_json"},
            "description": "pytest-created, proves OSDK codegen against a real struct property",
        },
    )
    assert status == 201, object_type

    value_type_name = _unique_name("OsdkCity")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/value-types", token=msmith_token,
        body={"name": value_type_name, "base_type": "string"},
    )
    assert status == 201

    comment_value_type_name = _unique_name("OsdkCommentType")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/value-types", token=msmith_token,
        body={"name": comment_value_type_name, "base_type": "string"},
    )
    assert status == 201

    shared_property_type_name = _unique_name("osdk_review_comment")
    status, _ = _request(
        "POST", f"{KNOWLEDGE}/shared-property-types", token=msmith_token,
        body={
            "api_name": shared_property_type_name, "display_name": "Review comment",
            "value_type": comment_value_type_name, "description": "Free-text reviewer comment",
        },
    )
    assert status == 201

    status, draft = _request(
        "POST", f"{KNOWLEDGE}/ontology/{object_type_name}/versions", token=msmith_token,
        body={"property_types": {
            "address": {"kind": "struct", "properties": {"city": {"kind": "value_type", "value_type": value_type_name}}},
            "comment": {"kind": "shared_property_type", "shared_property_type": shared_property_type_name},
        }},
    )
    assert status == 201, draft
    status, published = _request(
        "POST", f"{KNOWLEDGE}/ontology/{object_type_name}/versions/{draft['version']}/publish", token=msmith_token
    )
    assert status == 200, published

    action_name = f"{object_type_name}.setPriority"
    status, action_type = _request(
        "POST", f"{KNOWLEDGE}/action-types", token=msmith_token,
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
        "POST", f"{KNOWLEDGE}/interfaces", token=msmith_token,
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
    assert f'/interfaces/{interface_name}/objects"' in output, output

    module_path = tmp_path / "holon_ontology_test_output.py"
    module_path.write_text(output)
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(tmp_path)!r}); sys.path.insert(0, {str(Path(__file__).resolve().parent.parent / 'libs')!r}); import holon_ontology_test_output"],
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
    assert f"`${{knowledgeUrl}}/interfaces/{interface_name}/objects`" in output, output

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


def test_hardcoded_actions_use_their_specific_route_not_the_generic_one(jdoe_token: str) -> None:
    """`putOnCreditHold`/`closeAccount` must generate a call to their own
    specific route (bare local name), never the generic declarative-
    Action route (which needs the full dotted name and a `parameters`
    body field that endpoint doesn't even accept).
    """
    schema = fetch_schema(knowledge_url=KNOWLEDGE, token=jdoe_token)
    output = emit_python(schema)
    assert 'f"{knowledge_url}/objects/Customer/{instance_id}/actions/putOnCreditHold"' in output, output
    assert 'f"{knowledge_url}/objects/Customer/{instance_id}/actions/Customer.putOnCreditHold"' not in output, output
    assert 'body={"reason": reason},' in output, output


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
