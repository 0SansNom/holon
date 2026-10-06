"""Knowledge authz backfill walks every filiale stored in the catalogue."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "libs"))
sys.path.insert(0, str(REPO_ROOT / "services" / "knowledge"))

from app.ontology import authz_seed  # noqa: E402


class _Pool:
    async def fetch(self, sql: str):
        if sql == authz_seed._SCOPED_URNS_SQL:
            return [
                {"urn": "hl:acme:main:object-type:Customer"},
                {"urn": "hl:beta:east:object-type:Invoice"},
                {"urn": "hl:beta:east:relation-type:Invoice.customer"},
            ]
        if sql == authz_seed._TENANT_SQL:
            return [{"tenant_id": "acme"}, {"tenant_id": "beta"}, {"tenant_id": "gamma"}]
        raise AssertionError(sql)


class _Client:
    def __init__(self) -> None:
        self.writes: list[dict] = []

    async def write_relationship(self, **kwargs) -> None:
        self.writes.append(kwargs)

    async def set_single_subject(self, **kwargs) -> None:
        self.writes.append(kwargs)


def _patch_lists(monkeypatch) -> None:
    async def object_types(_pool, tenant_id: str):
        rows = {
            "acme": [{"urn": "hl:acme:main:object-type:Customer", "project_urn": None}],
            "beta": [{"urn": "hl:beta:east:object-type:Invoice", "project_urn": None}],
            "gamma": [],
        }
        return rows.get(tenant_id, [])

    async def relations(_pool, tenant_id: str):
        if tenant_id == "beta":
            return [{"urn": "hl:beta:east:relation-type:Invoice.customer", "project_urn": None}]
        return []

    async def spts(_pool, tenant_id: str):
        if tenant_id == "gamma":
            return [{"api_name": "email", "project_urn": None}]
        return []

    async def value_types(_pool, _tenant_id: str):
        return []

    monkeypatch.setattr(authz_seed, "list_object_types", object_types)
    monkeypatch.setattr(authz_seed, "list_relation_types", relations)
    monkeypatch.setattr(authz_seed, "list_shared_property_types", spts)
    monkeypatch.setattr(authz_seed, "list_value_types", value_types)


def test_two_tenants_are_seeded_from_their_urn_workspace(monkeypatch) -> None:
    _patch_lists(monkeypatch)
    client = _Client()

    async def run() -> None:
        await authz_seed.ensure_authz_seeded_all(client, _Pool(), "acme", "main")

    asyncio.run(run())
    parents = {
        (write["resource_urn"], write["subject_urn"])
        for write in client.writes
        if write["relation"] == "parent_workspace"
    }
    assert ("hl:acme:main:object-type:Customer", "hl:acme:global:workspace:main") in parents
    assert ("hl:beta:east:object-type:Invoice", "hl:beta:global:workspace:east") in parents
    assert ("hl:beta:east:relation-type:Invoice.customer", "hl:beta:global:workspace:east") in parents
    assert ("hl:gamma:global:shared-property-type:email", "hl:gamma:global:workspace:main") in parents
    assert not any(subject.endswith(":workspace:main") and resource.startswith("hl:beta:") for resource, subject in parents)
    projects = {
        write["resource_urn"]: write["subject_urn"] for write in client.writes if write["relation"] == "parent_project"
    }
    assert projects["hl:acme:main:object-type:Customer"] is None


def test_global_parent_prefers_the_designated_workspace() -> None:
    assert authz_seed.global_parent_workspace({"aaa", "main"}, "main") == "main"
    assert authz_seed.global_parent_workspace({"east"}, "main") == "east"
    assert authz_seed.global_parent_workspace({"aaa", "zzz"}, "main") == "main"
