"""HTTP: property_types render_hints + type_classes validated at publish."""

from __future__ import annotations

from conftest import CONNECTIVITY, KNOWLEDGE, _request, _unique_name, ontology_url, holon_url

REVIEWS_WITH_TAGS_API = "http://reviews-api:8000/reviews_with_tags.json"


def _register_sync_and_create_object_type(msmith_token: str, jdoe_token: str) -> str:
    source_name = _unique_name("render_hints_src")
    status, registration = _request(
        "POST",
        f"{CONNECTIVITY}/sources",
        token=jdoe_token,
        body={"name": source_name, "base_url": REVIEWS_WITH_TAGS_API},
    )
    assert status == 200, registration

    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": source_name})
    assert status == 200, result

    object_type_name = _unique_name("RenderHintsOT")
    status, object_type = _request(
        "POST",
        holon_url("/object-types"),
        token=msmith_token,
        body={
            "name": object_type_name,
            "source_dataset_urn": result["dataset_urn"],
            "property_mapping": {"id": "id", "comment": "comment"},
            "description": "pytest-created",
        },
    )
    assert status == 201, object_type
    return object_type_name


def test_publish_accepts_render_hints_and_type_classes(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    status, draft = _request(
        "POST",
        ontology_url(f"/objectTypes/{object_type_name}/versions"),
        token=msmith_token,
        body={
            "property_types": {
                "comment": {
                    "visibility": "prominent",
                    "render_hints": ["searchable", "sortable", "selectable"],
                    "type_classes": ["priority"],
                }
            }
        },
    )
    assert status == 201, draft
    status, published = _request(
        "POST",
        ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 200, published
    assert published["property_types"]["comment"]["render_hints"] == [
        "searchable",
        "sortable",
        "selectable",
    ]
    assert published["property_types"]["comment"]["type_classes"] == ["priority"]


def test_publish_rejects_unknown_render_hint(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    status, draft = _request(
        "POST",
        ontology_url(f"/objectTypes/{object_type_name}/versions"),
        token=msmith_token,
        body={"property_types": {"comment": {"render_hints": ["searchable", "glow"]}}},
    )
    assert status == 201, draft
    status, result = _request(
        "POST",
        ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 400, result
    assert "render_hints" in result["detail"], result


def test_publish_rejects_invalid_type_class(msmith_token: str, jdoe_token: str) -> None:
    object_type_name = _register_sync_and_create_object_type(msmith_token, jdoe_token)
    status, draft = _request(
        "POST",
        ontology_url(f"/objectTypes/{object_type_name}/versions"),
        token=msmith_token,
        body={"property_types": {"comment": {"type_classes": ["Bad Class!"]}}},
    )
    assert status == 201, draft
    status, result = _request(
        "POST",
        ontology_url(f"/objectTypes/{object_type_name}/versions/{draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 400, result
    assert "type class" in result["detail"], result
