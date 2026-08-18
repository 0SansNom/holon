"""Tags, featured flags, and project pins — Experience resource surfaces."""

from __future__ import annotations

from conftest import EXPERIENCE, IDENTITY, TENANT_ID, _request, _unique_name


def test_resource_tags_and_featured_round_trip(jdoe_token: str) -> None:
    urn = f"hl:{TENANT_ID}:main:object-type:Customer"
    status, tagged = _request(
        "PUT", f"{EXPERIENCE}/api/resources/{urn}/tags", token=jdoe_token, body={"tags": ["finance", "core"]},
    )
    assert status == 200, tagged
    assert set(tagged["tags"]) == {"finance", "core"}, tagged

    status, featured = _request("POST", f"{EXPERIENCE}/api/resources/{urn}/featured", token=jdoe_token)
    assert status == 200, featured
    assert featured["featured"] is True, featured

    status, listed = _request("GET", f"{EXPERIENCE}/api/resources?tag=finance&featured=true", token=jdoe_token)
    assert status == 200, listed
    assert any(row["resource_urn"] == urn for row in listed), listed

    _request("PUT", f"{EXPERIENCE}/api/resources/{urn}/tags", token=jdoe_token, body={"tags": []})
    _request("DELETE", f"{EXPERIENCE}/api/resources/{urn}/featured", token=jdoe_token)


def test_project_pins_hide_resources_the_viewer_cannot_read(msmith_token: str, jdoe_token: str, kenji_token: str) -> None:
    """Same discipline as collections: a pin is metadata that can leak a URN."""
    project_name = _unique_name("pins")
    status, project = _request("POST", f"{IDENTITY}/projects", token=msmith_token, body={"name": project_name})
    assert status == 201, project
    project_urn = project["urn"]
    visible = f"hl:{TENANT_ID}:main:object-type:Customer"
    hidden = f"hl:{TENANT_ID}:main:application:{_unique_name('secret-app')}"

    status, pinned = _request(
        "POST", f"{EXPERIENCE}/api/projects/{project_urn}/pins/{visible}", token=jdoe_token,
    )
    assert status == 200, pinned
    status, pinned_hidden = _request(
        "POST", f"{EXPERIENCE}/api/projects/{project_urn}/pins/{hidden}", token=jdoe_token,
    )
    assert status == 200, pinned_hidden

    status, owner = _request("GET", f"{EXPERIENCE}/api/projects/{project_urn}/pins", token=jdoe_token)
    assert status == 200, owner
    owner_urns = {row["resource_urn"] for row in owner}
    assert visible in owner_urns, owner
    assert hidden not in owner_urns, owner

    status, viewer = _request("GET", f"{EXPERIENCE}/api/projects/{project_urn}/pins", token=kenji_token)
    assert status == 200, viewer
    viewer_urns = {row["resource_urn"] for row in viewer}
    assert visible in viewer_urns, viewer
    assert hidden not in viewer_urns, viewer
