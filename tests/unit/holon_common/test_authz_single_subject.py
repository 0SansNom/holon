"""`set_single_subject` keeps exactly one subject on a single-valued relation."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from holon_common.authz import PermissionClient  # noqa: E402
from holon_common.spicedb_id import spicedb_object_id  # noqa: E402

_APP = "hl:acme:main:application:sales_board"
_PROJECT_A = "hl:acme:main:project:north_region"
_PROJECT_B = "hl:acme:main:project:south_region"


class _FakeSpiceDB:
    """Stores tuples by encoded object id, as SpiceDB does."""

    def __init__(self, tuples: set[tuple[str, str, str, str, str]]) -> None:
        self.tuples = set(tuples)
        self.write_requests: list[list[dict]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/v1/relationships/read":
            flt = body["relationshipFilter"]
            lines = [
                json.dumps(
                    {
                        "result": {
                            "relationship": {
                                "resource": {"objectType": rtype, "objectId": rid},
                                "relation": relation,
                                "subject": {"object": {"objectType": stype, "objectId": sid}},
                            }
                        }
                    }
                )
                for rtype, rid, relation, stype, sid in sorted(self.tuples)
                if rtype == flt["resourceType"]
                and rid == flt["optionalResourceId"]
                and relation == flt.get("optionalRelation", relation)
            ]
            return httpx.Response(200, text="\n".join(lines))
        updates = body["updates"]
        self.write_requests.append(updates)
        for update in updates:
            rel = update["relationship"]
            key = (
                rel["resource"]["objectType"],
                rel["resource"]["objectId"],
                rel["relation"],
                rel["subject"]["object"]["objectType"],
                rel["subject"]["object"]["objectId"],
            )
            if update["operation"] == "OPERATION_TOUCH":
                self.tuples.add(key)
            else:
                self.tuples.discard(key)
        return httpx.Response(200, json={})


def _parent(project_urn: str) -> tuple[str, str, str, str, str]:
    return ("application", spicedb_object_id(_APP), "parent_project", "project", spicedb_object_id(project_urn))


def _set(spicedb: _FakeSpiceDB, project_urn: str | None) -> None:
    client = PermissionClient("http://spicedb", "key", "http://opa")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(spicedb.handle))
    asyncio.run(
        client.set_single_subject(
            resource_type="application",
            resource_urn=_APP,
            relation="parent_project",
            subject_type="project",
            subject_urn=project_urn,
        )
    )


def test_moving_to_another_project_removes_the_old_edge_in_one_write() -> None:
    spicedb = _FakeSpiceDB({_parent(_PROJECT_A)})

    _set(spicedb, _PROJECT_B)

    assert spicedb.tuples == {_parent(_PROJECT_B)}
    assert len(spicedb.write_requests) == 1
    assert sorted(update["operation"] for update in spicedb.write_requests[0]) == [
        "OPERATION_DELETE",
        "OPERATION_TOUCH",
    ]


def test_clearing_the_project_removes_every_edge() -> None:
    spicedb = _FakeSpiceDB({_parent(_PROJECT_A), _parent(_PROJECT_B)})

    _set(spicedb, None)

    assert spicedb.tuples == set()


def test_unchanged_project_does_not_write() -> None:
    spicedb = _FakeSpiceDB({_parent(_PROJECT_A)})

    _set(spicedb, _PROJECT_A)

    assert spicedb.tuples == {_parent(_PROJECT_A)}
    assert spicedb.write_requests == []


def test_first_project_is_written() -> None:
    spicedb = _FakeSpiceDB(set())

    _set(spicedb, _PROJECT_A)

    assert spicedb.tuples == {_parent(_PROJECT_A)}
