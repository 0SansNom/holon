"""Identity rules that do not need Postgres, SpiceDB, or the stack.

Fake pools and clients stand in for the registries. HTTP behavior of the
routers stays in tests/integration/identity.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# Pydantic evaluates `str | None` on the Identity models. CI is 3.11.
if sys.version_info < (3, 10):
    pytest.skip("identity models need Python 3.10+", allow_module_level=True)

from app import deps, federation, governance, oidc, saml, seed, status_events, token_revocation  # noqa: E402
from holon_common import HolonError, Principal  # noqa: E402
from holon_common.correlation import set_correlation_id  # noqa: E402
from holon_common.spicedb_id import spicedb_object_id  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _principal(urn: str = "hl:acme:global:user:admin", type_: str = "user", tenant: str = "acme") -> Principal:
    return Principal(urn=urn, type=type_, tenant_id=tenant, display_name=urn.rsplit(":", 1)[-1])


# --- federation claims ---


def test_federation_claim_edges(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_OIDC_TENANT_CLAIM", "tenant_id")
    monkeypatch.setenv("HOLON_OIDC_TENANT_GROUP_PREFIX", "tenant:")
    assert federation.tenant_from_claims({"tenant_id": ""}, default_tenant="acme") == "acme"
    assert federation.tenant_from_claims({"roles": "tenant:filiale"}, default_tenant="acme") == "filiale"
    assert federation.tenant_from_claims({"groups": [1, "eng"]}, default_tenant="acme") == "acme"

    assert federation.urn_safe_local_name("..Alice Smith", fallback="sso-user") == "Alice-Smith"
    assert federation.urn_safe_local_name("...", fallback="sso-user") == "sso-user"
    assert federation.local_name_from_claims({}) == "unknown"
    assert federation.display_name_from_claims({"name": "Ada"}) == "Ada"
    assert federation.display_name_from_claims({"email": "a@b.c"}) == "a@b.c"
    assert federation.display_name_from_claims({"preferred_username": "ada"}) == "ada"
    assert federation.display_name_from_claims({"sub": "sub-1"}) == "sub-1"
    assert federation.display_name_from_claims({}) == "SSO User"
    assert federation.groups_from_claims({"roles": "one"}) == ["one"]
    assert federation.groups_from_claims({"groups": ["a", 1]}) == ["a", "1"]
    assert federation.groups_from_claims({}) == []

    monkeypatch.setenv("HOLON_OIDC_WORKSPACE_ADMIN_GROUP_PREFIX", "workspace-admin:")
    monkeypatch.setenv("HOLON_OIDC_WORKSPACE_EDITOR_GROUP_PREFIX", "workspace-editor:")
    monkeypatch.setenv("HOLON_OIDC_WORKSPACE_GROUP_PREFIX", "workspace:")
    roles = federation.workspace_roles_from_claims(
        {"groups": ["workspace-admin:", "workspace:main", "workspace-admin:main", "noise"]}
    )
    assert "workspace-admin:" not in roles
    assert roles["main"] == "admin"


# --- saml settings and response handling ---


def test_saml_request_settings_and_response(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_SAML_SP_ACS_URL", raising=False)
    monkeypatch.delenv("HOLON_SAML_SP_ENTITY_ID", raising=False)
    monkeypatch.delenv("HOLON_SAML_SP_CERT", raising=False)
    monkeypatch.setenv("HOLON_SAML_SP_PRIVATE_KEY", "pem")
    assert saml._acs_url().endswith("/saml/acs")
    monkeypatch.setenv("HOLON_SAML_SP_ACS_URL", "https://sp/acs")
    settings = saml._sp_settings()
    assert settings["assertionConsumerService"]["url"] == "https://sp/acs"
    assert settings["privateKey"] == "pem"
    prepared = saml._prepare_request(https=True, http_host="sp", script_name="/saml/acs", post_params={"SAMLResponse": "x"})
    assert prepared["https"] == "on" and prepared["server_port"] == "443"
    plain = saml._prepare_request(https=False, http_host="sp", script_name="/saml/acs", post_params={})
    assert plain["https"] == "off" and plain["server_port"] == "80"

    class _Parser:
        @staticmethod
        def parse(xml):
            return {"idp": {"entityId": xml}}

        @staticmethod
        def parse_remote(url):
            return {"idp": {"entityId": url}}

    monkeypatch.setitem(
        __import__("sys").modules,
        "onelogin.saml2.idp_metadata_parser",
        type("M", (), {"OneLogin_Saml2_IdPMetadataParser": _Parser}),
    )
    monkeypatch.setenv("HOLON_SAML_IDP_METADATA_XML", "<xml/>")
    assert saml._idp_settings()["entityId"] == "<xml/>"
    monkeypatch.delenv("HOLON_SAML_IDP_METADATA_XML", raising=False)
    monkeypatch.setenv("HOLON_SAML_IDP_METADATA_URL", "https://idp/meta")
    assert saml._settings_dict()["idp"]["entityId"] == "https://idp/meta"

    state = {"errors": [], "authenticated": True, "assertion_id": "aid"}

    class _Auth:
        def __init__(self, req, settings):
            self.req = req

        def login(self, return_to=None):
            return "https://idp/sso"

        def process_response(self):
            return None

        def get_errors(self):
            return state["errors"]

        def get_last_error_reason(self):
            return "expired"

        def is_authenticated(self):
            return state["authenticated"]

        def get_nameid(self):
            return "user-1"

        def get_attributes(self):
            return {"mail": ["a@b.c"]}

        def get_last_assertion_id(self):
            return state["assertion_id"]

    monkeypatch.setitem(
        __import__("sys").modules,
        "onelogin.saml2.auth",
        type("M", (), {"OneLogin_Saml2_Auth": _Auth}),
    )
    assert saml.build_login_redirect(https=False, http_host="sp", script_name="/saml/acs") == "https://idp/sso"
    claims = saml.process_acs_response(https=True, http_host="sp", script_name="/saml/acs", post_params={"SAMLResponse": "x"})
    assert claims["email"] == "a@b.c"
    assert claims["_assertion_id"] == "aid"
    state["assertion_id"] = ""
    assert "_assertion_id" not in saml.process_acs_response(
        https=False, http_host="sp", script_name="/saml/acs", post_params={}
    )
    state["errors"] = ["invalid"]
    with pytest.raises(ValueError, match="SAML response invalid"):
        saml.process_acs_response(https=False, http_host="sp", script_name="/saml/acs", post_params={})
    state["errors"] = []
    state["authenticated"] = False
    with pytest.raises(ValueError, match="not authenticated"):
        saml.process_acs_response(https=False, http_host="sp", script_name="/saml/acs", post_params={})

    class _Settings:
        def __init__(self, data, sp_validation_only=False):
            self.data = data

        def get_sp_metadata(self):
            return "<EntityDescriptor/>"

        def validate_metadata(self, metadata):
            return ["missing cert"] if metadata == "bad" else []

    monkeypatch.setitem(
        __import__("sys").modules,
        "onelogin.saml2.settings",
        type("M", (), {"OneLogin_Saml2_Settings": _Settings}),
    )
    assert saml.build_sp_metadata_xml().startswith("<Entity")


def test_saml_metadata_validation_failure(monkeypatch) -> None:
    class _Settings:
        def __init__(self, data, sp_validation_only=False):
            pass

        def get_sp_metadata(self):
            return "bad"

        def validate_metadata(self, metadata):
            return ["missing cert"]

    monkeypatch.setitem(
        __import__("sys").modules,
        "onelogin.saml2.settings",
        type("M", (), {"OneLogin_Saml2_Settings": _Settings}),
    )
    monkeypatch.setenv("HOLON_SAML_SP_PRIVATE_KEY", "")
    with pytest.raises(ValueError, match="invalid SP metadata"):
        saml.build_sp_metadata_xml()


# --- oidc ---


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    def __init__(self, *, get=None, post=None):
        self._get = get
        self._post = post

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, headers=None):
        return self._get(url, headers)

    async def post(self, url, data=None):
        return self._post(url, data)


def test_oidc_discovery_pkce_and_exchange(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_OIDC_ISSUER", raising=False)
    assert oidc.oidc_enabled() is False
    monkeypatch.setenv("HOLON_OIDC_ISSUER", "https://idp.example/")
    assert oidc.oidc_enabled() is True
    monkeypatch.setenv("HOLON_OIDC_CLIENT_SECRET", "plain")
    assert oidc._client_secret() == "plain"
    monkeypatch.setenv("HOLON_OIDC_CLIENT_SECRET", "env:OIDC_SECRET")
    monkeypatch.setenv("OIDC_SECRET", "resolved")
    monkeypatch.delenv("HOLON_SECRET_BACKEND", raising=False)
    assert oidc._client_secret() == "resolved"
    verifier, challenge = oidc._pkce_pair()
    assert verifier and "=" not in challenge

    meta = {
        "authorization_endpoint": "https://idp/auth",
        "token_endpoint": "https://idp/token",
        "userinfo_endpoint": "https://idp/userinfo",
    }

    def _install(get, post=None):
        monkeypatch.setattr(httpx, "AsyncClient", lambda **_k: _Client(get=get, post=post))

    _install(lambda url, headers: _Response(meta))
    discovered = _run(oidc.discover())
    assert discovered["authorization_endpoint"].endswith("/auth")
    _install(lambda url, headers: _Response({}, status=500))
    with pytest.raises(RuntimeError):
        _run(oidc.discover())

    stored: dict = {}

    class _Conn:
        def transaction(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        async def execute(self, sql, *args):
            if "INSERT" in sql:
                stored["row"] = args

    class _Pool:
        def acquire(self):
            return _Conn()

        async def fetchrow(self, sql, state):
            if state == "missing":
                return None
            return {"verifier": "v", "redirect_uri": "https://app/cb"}

    monkeypatch.setenv("HOLON_OIDC_CLIENT_ID", "client")
    monkeypatch.delenv("HOLON_OIDC_SCOPES", raising=False)
    _install(lambda url, headers: _Response(meta))
    built = _run(oidc.build_authorize_url(_Pool(), redirect_uri="https://app/cb"))
    assert built["state"]
    assert "code_challenge_method=S256" in built["authorize_url"]
    assert stored["row"][2] == "https://app/cb"

    def _post(url, data):
        return _Response({"access_token": "tok"})

    def _get(url, headers):
        if url.endswith("/userinfo"):
            assert headers["Authorization"] == "Bearer tok"
            return _Response({"sub": "user-1", "email": "a@b.c"})
        return _Response(meta)

    _install(_get, _post)
    claims = _run(oidc.exchange_code(_Pool(), code="c", state="ok"))
    assert claims["sub"] == "user-1"
    with pytest.raises(ValueError, match="invalid or expired"):
        _run(oidc.exchange_code(_Pool(), code="c", state="missing"))

    _install(_get, lambda url, data: _Response({}))
    with pytest.raises(ValueError, match="access_token"):
        _run(oidc.exchange_code(_Pool(), code="c", state="ok"))

    def _get_nosub(url, headers):
        if url.endswith("/userinfo"):
            return _Response({"email": "a@b.c"})
        return _Response(meta)

    _install(_get_nosub, _post)
    with pytest.raises(ValueError, match="sub"):
        _run(oidc.exchange_code(_Pool(), code="c", state="ok"))


# --- seed registry ---


class _Db:
    def __init__(self) -> None:
        self.tenants: dict = {}
        self.workspaces: dict = {}
        self.projects: dict = {}
        self.principals: dict = {}
        self.update_result = "UPDATE 1"

    async def execute(self, sql, *args):
        compact = " ".join(sql.split())
        if compact.startswith("INSERT INTO tenant"):
            self.tenants[args[0]] = {"tenant_id": args[0], "display_name": args[1], "status": "active"}
        elif compact.startswith("UPDATE tenant"):
            self.tenants[args[0]]["status"] = args[1]
        elif compact.startswith("INSERT INTO workspace"):
            self.workspaces[args[0]] = {
                "workspace_id": args[0], "tenant_id": args[1], "display_name": args[2], "status": "active",
            }
        elif compact.startswith("UPDATE workspace"):
            self.workspaces[args[0]]["status"] = args[1]
        elif compact.startswith("INSERT INTO project"):
            self.projects[args[0]] = {
                "urn": args[0], "tenant_id": args[1], "workspace_id": args[2], "name": args[3],
            }
        elif compact.startswith("INSERT INTO principal"):
            urn = args[0]
            if "oidc_sub" in compact:
                self.principals[urn] = {
                    "urn": urn, "type": args[1], "tenant_id": args[2], "display_name": args[3],
                    "on_behalf_of": args[4], "country": args[5], "status": "active",
                    "client_secret_hash": args[6], "oidc_sub": args[7], "external_id": args[8],
                }
            else:
                self.principals[urn] = {
                    "urn": urn, "type": "user", "tenant_id": args[1], "display_name": args[2],
                    "country": args[3], "status": "active", "client_secret_hash": args[4],
                }
        elif compact.startswith("UPDATE principal SET client_secret"):
            self.principals[args[1]]["client_secret_hash"] = args[0]
            self.principals[args[1]]["status"] = "active"
            return self.update_result
        elif compact.startswith("UPDATE principal SET status"):
            if args[0] not in self.principals:
                return "UPDATE 0"
            self.principals[args[0]]["status"] = args[1]
        return "UPDATE 1"

    async def fetchrow(self, sql, *args):
        if "FROM tenant" in sql:
            return self.tenants.get(args[0])
        if "FROM workspace" in sql:
            return self.workspaces.get(args[0])
        if "FROM project" in sql:
            return self.projects.get(args[0])
        if "FROM principal" in sql:
            return self.principals.get(args[0])
        return None

    async def fetch(self, sql, *args):
        if "FROM principal" in sql:
            return list(self.principals.values())
        if "FROM project" in sql:
            return [row for row in self.projects.values() if row["tenant_id"] == args[0]]
        if "FROM tenant" in sql:
            return list(self.tenants.values())
        if "FROM workspace" in sql and args:
            return [row for row in self.workspaces.values() if row["tenant_id"] == args[0]]
        if "FROM workspace" in sql:
            return list(self.workspaces.values())
        return []


def test_seed_hash_edges_and_registry(monkeypatch) -> None:
    assert seed._verify_client_secret_hash("x", "not-a-hash") is False
    assert seed._verify_client_secret_hash("x", "nope$1$aa$bb") is False
    assert seed._verify_client_secret_hash("x", "pbkdf2$1$abc$YQ") is False
    assert seed.tenant_urn("acme") == "hl:acme:global:tenant:acme"
    assert seed.workspace_urn("acme", "main") == "hl:acme:global:workspace:main"
    assert seed.project_urn("acme", "main", "billing").endswith(":project:billing")
    monkeypatch.delenv("HOLON_FLAG", raising=False)
    assert seed._truthy("HOLON_FLAG") is False
    monkeypatch.setenv("HOLON_FLAG", " YES ")
    assert seed._truthy("HOLON_FLAG") is True
    monkeypatch.delenv("HOLON_BOOTSTRAP_ADMIN_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="HOLON_BOOTSTRAP_ADMIN_SECRET"):
        seed._require_bootstrap_admin_secret()
    monkeypatch.setenv("HOLON_BOOTSTRAP_ADMIN_SECRET", "  secret  ")
    assert seed._require_bootstrap_admin_secret() == "secret"

    db = _Db()
    assert _run(seed.get_tenant(db, "acme")) is None
    created = _run(seed.create_tenant(db, tenant_id="acme", display_name="Acme"))
    assert created["display_name"] == "Acme"
    assert _run(seed.list_tenants(db))[0]["tenant_id"] == "acme"
    assert _run(seed.set_tenant_status(db, "acme", "disabled"))["status"] == "disabled"

    assert _run(seed.get_workspace(db, "main")) is None
    workspace = _run(seed.create_workspace(db, tenant_id="acme", workspace_id="main", display_name="Main"))
    assert workspace["display_name"] == "Main"
    assert _run(seed.list_workspaces(db))[0]["workspace_id"] == "main"
    assert _run(seed.list_workspaces(db, "acme"))[0]["tenant_id"] == "acme"
    assert _run(seed.list_workspaces(db, "other")) == []
    assert _run(seed.set_workspace_status(db, "main", "disabled"))["status"] == "disabled"

    project = _run(seed.create_project(db, tenant_id="acme", workspace_id="main", name="billing"))
    assert project["name"] == "billing"
    assert _run(seed.get_project(db, "missing")) is None
    assert _run(seed.list_projects(db, "acme"))[0]["name"] == "billing"

    principal = _run(seed.insert_principal(
        db, tenant_id="acme", type="service-account", local_name="scim", display_name="SCIM",
        client_secret="secret-1", oidc_sub="sub", external_id="ext",
    ))
    assert ":service-account:scim" in principal["urn"]
    assert principal["client_secret"] == "secret-1"
    generated = _run(seed.insert_principal(
        db, tenant_id="acme", type="service_account", local_name="bot", display_name="Bot",
    ))
    assert generated["client_secret"]
    user = _run(seed.insert_principal(
        db, tenant_id="acme", type="user", local_name="ada", display_name="Ada",
    ))
    assert user["urn"].endswith(":user:ada")
    assert _run(seed.set_principal_status(db, principal["urn"], "disabled"))["status"] == "disabled"
    assert _run(seed.set_principal_status(db, "hl:acme:global:user:missing", "active")) is None

    _run(seed._ensure_bootstrap_tenant_row(db, "acme"))
    _run(seed._ensure_bootstrap_workspace_row(db, "acme", "main"))
    monkeypatch.setenv("HOLON_BOOTSTRAP_ADMIN_LOCAL_NAME", "   ")
    monkeypatch.setenv("HOLON_BOOTSTRAP_ADMIN_RESET_SECRET", "1")
    admin_urn = "hl:acme:global:user:admin"
    db.principals[admin_urn] = {"urn": admin_urn, "status": "disabled", "tenant_id": "acme"}
    db.update_result = "UPDATE 0"

    class _Authz:
        def __init__(self):
            self.writes = []
            self.rels = [{
                "subject": {"object": {"objectType": "user", "objectId": "ignored"}},
            }, {
                "subject": {"object": {"objectType": "principal", "objectId": spicedb_object_id(admin_urn)}},
            }]

        async def read_relationships(self, **_kwargs):
            return self.rels

        async def write_relationship(self, **kwargs):
            self.writes.append(kwargs)

    authz = _Authz()
    _run(seed.ensure_instance_bootstrap(db, authz, tenant_id="acme", workspace_id="main"))
    assert any(call["relation"] == "admin" for call in authz.writes)
    assert db.principals[admin_urn]["status"] == "active"
    db.principals[admin_urn]["status"] = "active"
    db.update_result = "UPDATE 0"
    _run(seed.ensure_instance_bootstrap(db, authz, tenant_id="acme", workspace_id="main"))

    fresh = _Db()
    _run(seed._ensure_bootstrap_tenant_row(fresh, "acme"))
    assert fresh.tenants["acme"]["display_name"] == "acme"
    _run(seed._ensure_bootstrap_workspace_row(fresh, "acme", "main"))
    assert fresh.workspaces["main"]["display_name"] == "main"
    fresh.workspaces["other"] = {"workspace_id": "other", "tenant_id": "elsewhere", "display_name": "x", "status": "active"}
    with pytest.raises(RuntimeError, match="belongs to tenant"):
        _run(seed._ensure_bootstrap_workspace_row(fresh, "acme", "other"))
    _run(seed._ensure_bootstrap_admin_principal(
        fresh, tenant_id="acme", admin_urn="hl:acme:global:user:admin", secret="secret",
    ))
    assert fresh.principals["hl:acme:global:user:admin"]["status"] == "active"


# --- governance ---


def _decision(allowed: bool, reason: str = "no"):
    decision = MagicMock()
    decision.allowed = allowed
    decision.reason = reason
    return decision


def test_governance_validation_and_authorization(monkeypatch) -> None:
    governance._validate_relation("editor")
    with pytest.raises(HolonError) as invalid:
        governance._validate_relation("owner")
    assert invalid.value.status_code == 400
    governance._validate_project_relation("admin")
    with pytest.raises(HolonError):
        governance._validate_project_relation("owner")

    actor = _principal()
    deps.authz = MagicMock()
    deps.authz.authorize = AsyncMock(return_value=_decision(True))
    _run(governance._authorize_bootstrap_governance(actor))
    deps.authz.authorize = AsyncMock(return_value=_decision(False, "denied"))
    with pytest.raises(HolonError) as denied:
        _run(governance._authorize_bootstrap_governance(actor))
    assert denied.value.status_code == 403

    monkeypatch.setattr(governance, "get_workspace", AsyncMock(return_value=None))
    with pytest.raises(HolonError) as missing:
        _run(governance._authorize_workspace_governance(actor, "acme", "main"))
    assert missing.value.status_code == 404
    monkeypatch.setattr(governance, "get_workspace", AsyncMock(return_value={"tenant_id": "other", "status": "active"}))
    with pytest.raises(HolonError):
        _run(governance._authorize_workspace_governance(actor, "acme", "main"))
    monkeypatch.setattr(governance, "get_workspace", AsyncMock(return_value={"tenant_id": "acme", "status": "disabled"}))
    with pytest.raises(HolonError) as disabled:
        _run(governance._authorize_workspace_governance(actor, "acme", "main"))
    assert disabled.value.error_name == "WorkspaceDisabled"
    monkeypatch.setattr(governance, "get_workspace", AsyncMock(return_value={"tenant_id": "acme", "status": "active"}))
    deps.authz.authorize = AsyncMock(return_value=_decision(False))
    with pytest.raises(HolonError):
        _run(governance._authorize_workspace_governance(actor, "acme", "main"))
    deps.authz.authorize = AsyncMock(return_value=_decision(True))
    assert _run(governance._authorize_workspace_governance(actor, "acme", "main")).endswith(":workspace:main")

    response = MagicMock()
    response.status_code = 404
    missing_rel = httpx.HTTPStatusError("missing", request=MagicMock(), response=response)
    deps.authz.delete_relationship = AsyncMock(side_effect=missing_rel)
    _run(governance._delete_relationship_or_reraise(
        resource_type="workspace", resource_urn="urn", relation="viewer", subject_urn=actor.urn,
    ))
    response.status_code = 500
    deps.authz.delete_relationship = AsyncMock(side_effect=httpx.HTTPStatusError("down", request=MagicMock(), response=response))
    with pytest.raises(HolonError) as unavailable:
        _run(governance._delete_relationship_or_reraise(
            resource_type="workspace", resource_urn="urn", relation="viewer", subject_urn=actor.urn,
        ))
    assert unavailable.value.status_code == 503
    deps.authz.delete_relationship = AsyncMock(side_effect=httpx.RequestError("net"))
    with pytest.raises(HolonError):
        _run(governance._delete_relationship_or_reraise(
            resource_type="workspace", resource_urn="urn", relation="viewer", subject_urn=actor.urn,
        ))
    deps.authz.delete_relationship = AsyncMock()
    _run(governance._delete_relationship_or_reraise(
        resource_type="workspace", resource_urn="urn", relation="viewer", subject_urn=actor.urn,
    ))

    monkeypatch.setattr(governance, "list_workspaces", AsyncMock(return_value=[]))
    monkeypatch.setattr(governance, "_authorize_bootstrap_governance", AsyncMock())
    _run(governance._authorize_principal_governance(actor, "acme"))
    monkeypatch.setattr(governance, "list_workspaces", AsyncMock(return_value=[{"workspace_id": "ops"}]))
    monkeypatch.setattr(governance, "_authorize_workspace_governance", AsyncMock())
    _run(governance._authorize_principal_governance(actor, "filiale"))

    seen = []

    async def _auth_ws(_principal, tenant_id, workspace_id):
        seen.append(workspace_id)

    monkeypatch.setattr(governance, "_authorize_workspace_governance", _auth_ws)
    assert _run(governance._resolve_workspace_governance(actor, tenant_id="acme", workspace_id="ops")) == ("acme", "ops")
    assert _run(governance._resolve_workspace_governance(actor, tenant_id="acme", workspace_id=None))[1] == "main"
    monkeypatch.setattr(governance, "list_workspaces", AsyncMock(return_value=[]))
    with pytest.raises(HolonError) as empty:
        _run(governance._resolve_workspace_governance(actor, tenant_id="filiale", workspace_id=None))
    assert empty.value.error_name == "TenantHasNoWorkspace"

    async def _auth_second(_principal, tenant_id, workspace_id):
        if workspace_id == "a":
            raise HolonError.forbidden("PermissionDenied", "no")
        return "urn"

    monkeypatch.setattr(governance, "list_workspaces", AsyncMock(return_value=[{"workspace_id": "a"}, {"workspace_id": "c"}]))
    monkeypatch.setattr(governance, "_authorize_workspace_governance", _auth_second)
    assert _run(governance._resolve_workspace_governance(actor, tenant_id="filiale", workspace_id=None)) == ("filiale", "c")

    async def _auth_mixed(_principal, tenant_id, workspace_id):
        if workspace_id == "a":
            raise HolonError.forbidden("PermissionDenied", "no")
        if workspace_id == "b":
            raise HolonError.unavailable("SpiceDbUnavailable", "down")
        return "urn"

    monkeypatch.setattr(governance, "list_workspaces", AsyncMock(return_value=[{"workspace_id": "a"}, {"workspace_id": "b"}]))
    monkeypatch.setattr(governance, "_authorize_workspace_governance", _auth_mixed)
    with pytest.raises(HolonError) as outage:
        _run(governance._resolve_workspace_governance(actor, tenant_id="filiale", workspace_id=None))
    assert outage.value.status_code == 503
    monkeypatch.setattr(
        governance, "list_workspaces", AsyncMock(return_value=[{"workspace_id": "a"}]),
    )

    async def _only_forbidden(*_args):
        raise HolonError.forbidden("PermissionDenied", "no")

    monkeypatch.setattr(governance, "_authorize_workspace_governance", _only_forbidden)
    with pytest.raises(HolonError) as last:
        _run(governance._resolve_workspace_governance(actor, tenant_id="filiale", workspace_id=None))
    assert last.value.status_code == 403

    urn = "hl:acme:global:user:alice"
    deps.authz.read_relationships = AsyncMock(return_value=[
        {"relation": "parent_tenant", "subject": {"object": {"objectType": "tenant", "objectId": "x"}}},
        {"relation": "viewer", "subject": {"object": {"objectType": "principal", "objectId": "missing"}}},
        {"relation": "editor", "subject": {"object": {"objectType": "principal", "objectId": spicedb_object_id(urn)}}},
    ])
    deps.pool = MagicMock()
    deps.pool.fetch = AsyncMock(return_value=[{"urn": urn, "display_name": "Alice", "type": "user"}])
    grants = _run(governance._access_listing("workspace", "hl:acme:global:workspace:main", {"viewer", "editor"}))
    assert [g["relation"] for g in grants] == ["editor", "viewer"]
    assert grants[1]["display_name"] is None
    assert grants[0]["display_name"] == "Alice"

    monkeypatch.setattr(governance, "list_projects", AsyncMock(return_value=[]))
    with pytest.raises(HolonError) as no_project:
        _run(governance._authorize_project_governance(actor, "billing"))
    assert no_project.value.status_code == 404
    monkeypatch.setattr(governance, "list_projects", AsyncMock(return_value=[{"name": "billing", "urn": "hl:acme:main:project:billing"}]))
    deps.authz.authorize = AsyncMock(return_value=_decision(False, "no"))
    with pytest.raises(HolonError):
        _run(governance._authorize_project_governance(actor, "billing"))
    deps.authz.authorize = AsyncMock(return_value=_decision(True))
    assert _run(governance._authorize_project_governance(actor, "billing")).endswith(":billing")


# --- revocation, status, deps helpers ---


def test_revocation_snapshot_and_status_event(monkeypatch) -> None:
    class _Conn:
        def __init__(self, db):
            self.db = db

        def transaction(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        async def execute(self, sql, *args):
            self.db["sql"].append(sql)
            if "INSERT INTO revoked_token" in sql:
                self.db["jtis"].append(args[0])

        async def fetch(self, sql, *args):
            if "principal" in sql:
                return self.db["disabled"]
            return [{"jti": j} for j in self.db["jtis"]]

    db = {"sql": [], "jtis": [], "disabled": [{"urn": "hl:acme:global:user:old"}]}

    class _Pool:
        def acquire(self):
            return _Conn(db)

        async def fetchval(self, sql, jti):
            return 1 if jti in db["jtis"] else None

        async def fetchrow(self, sql, urn):
            if urn == "missing":
                return None
            return {"urn": urn, "status": "disabled"}

    pool = _Pool()
    assert _run(token_revocation.is_jti_revoked_in_db(pool, "")) is False
    assert _run(token_revocation.is_jti_revoked_in_db(pool, "nope")) is False
    actor = _principal()
    set_correlation_id("corr-1")
    enqueued = []

    async def _enqueue(_conn, event):
        enqueued.append(event)

    monkeypatch.setattr(token_revocation.outbox, "enqueue", _enqueue)
    _run(token_revocation.enqueue_token_revoked(
        pool, jti="jti-1", principal_urn=actor.urn, expires_at=datetime(2026, 1, 1),
        actor=actor, tenant_id="acme", workspace_id="main",
    ))
    assert enqueued[0].correlation_id == "corr-1"
    assert db["jtis"] == ["jti-1"]
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
    set_correlation_id(None)
    _run(token_revocation.enqueue_token_revoked(
        pool, jti="jti-2", principal_urn=actor.urn, expires_at=aware,
        actor=actor, tenant_id="acme", workspace_id="main",
    ))
    assert enqueued[1].correlation_id == enqueued[1].event_id
    assert _run(token_revocation.is_jti_revoked_in_db(pool, "jti-1")) is True
    snapshot = _run(token_revocation.load_revocation_snapshot(pool))
    assert snapshot["disabled_principal_urns"] == ["hl:acme:global:user:old"]
    assert "jti-1" in snapshot["revoked_jtis"]
    _run(token_revocation.hydrate_local_denylist_from_db(pool))

    monkeypatch.setattr(status_events.outbox, "enqueue", _enqueue)
    set_correlation_id("status-1")
    updated = _run(status_events.enqueue_principal_status_event(
        pool, target_principal_urn=actor.urn, status="disabled", actor=actor, tenant_id="acme", workspace_id="main",
    ))
    assert updated["status"] == "disabled"
    active = _run(status_events.enqueue_principal_status_event(
        pool, target_principal_urn=actor.urn, status="active", actor=actor, tenant_id="acme", workspace_id="main",
    ))
    assert active["urn"] == actor.urn
    assert _run(status_events.enqueue_principal_status_event(
        pool, target_principal_urn="missing", status="disabled", actor=actor, tenant_id="acme", workspace_id="main",
    )) is None


def test_deps_rate_limit_issue_and_principal_checks(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    deps._rate_limit_auth("local")
    monkeypatch.setenv("HOLON_ENV", "production")
    deps._AUTH_ATTEMPTS.clear()
    for _ in range(deps._AUTH_MAX_ATTEMPTS):
        deps._rate_limit_auth("alice")
    with pytest.raises(HolonError) as limited:
        deps._rate_limit_auth("alice")
    assert limited.value.status_code == 429
    deps._AUTH_ATTEMPTS.clear()
    for index in range(deps._AUTH_ATTEMPTS_MAX_KEYS + 2):
        deps._AUTH_ATTEMPTS[f"k{index}"] = [0.0]
    deps._rate_limit_auth("fresh")
    assert len(deps._AUTH_ATTEMPTS) <= deps._AUTH_ATTEMPTS_MAX_KEYS
    deps._AUTH_ATTEMPTS.clear()
    monkeypatch.delenv("HOLON_ENV", raising=False)

    principal = _principal()
    token = deps._issue(principal)
    assert token
    short = deps._issue(principal, ttl_seconds=30)
    assert short

    row = {
        "urn": principal.urn, "type": "user", "tenant_id": "acme", "display_name": "Admin",
        "client_secret": "s", "client_secret_hash": "h", "status": "active", "oidc_sub": None, "external_id": None,
        "on_behalf_of": None, "country": "FR",
    }
    built = deps._principal_from_row(row)
    assert built.country == "FR"
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    assert _run(deps._fetch_principal(pool, principal.urn)) is None
    pool.fetchrow = AsyncMock(return_value=row)
    assert _run(deps._fetch_principal(pool, principal.urn)).urn == principal.urn

    deps.pool = pool
    monkeypatch.setattr(deps, "get_tenant", AsyncMock(return_value={"status": "active"}))
    assert _run(deps._require_active_principal_row(principal.urn))["status"] == "active"
    pool.fetchrow = AsyncMock(return_value=None)
    with pytest.raises(HolonError):
        _run(deps._require_active_principal_row(principal.urn))
    pool.fetchrow = AsyncMock(return_value={**row, "status": "disabled"})
    with pytest.raises(HolonError):
        _run(deps._require_active_principal_row(principal.urn))
    pool.fetchrow = AsyncMock(return_value=row)
    monkeypatch.setattr(deps, "get_tenant", AsyncMock(return_value={"status": "disabled"}))
    with pytest.raises(HolonError):
        _run(deps._require_active_principal_row(principal.urn))
    monkeypatch.setattr(deps, "get_tenant", AsyncMock(return_value=None))
    with pytest.raises(HolonError):
        _run(deps._require_active_principal_row(principal.urn))

    deps._reject_group_authentication(row)
    with pytest.raises(HolonError) as group:
        deps._reject_group_authentication({**row, "type": "group"})
    assert group.value.error_name == "GroupCannotAuthenticate"

    request = MagicMock()
    monkeypatch.setattr(deps, "_base_principal", AsyncMock(return_value=principal))
    monkeypatch.setattr(deps, "is_jti_revoked_in_db", AsyncMock(return_value=False), raising=False)
    pool.fetchrow = AsyncMock(return_value={"status": "active", "tenant_id": "acme"})
    deps.pool = pool
    monkeypatch.setattr(deps, "get_tenant", AsyncMock(return_value={"status": "active"}))
    # current_principal imports is_jti_revoked_in_db inside the function.
    monkeypatch.setattr("app.token_revocation.is_jti_revoked_in_db", AsyncMock(return_value=False))
    principal.jti = "jti"
    assert _run(deps.current_principal(request)).urn == principal.urn
    monkeypatch.setattr("app.token_revocation.is_jti_revoked_in_db", AsyncMock(return_value=True))
    with pytest.raises(HolonError) as revoked:
        _run(deps.current_principal(request))
    assert revoked.value.error_name == "TokenRevoked"
    monkeypatch.setattr("app.token_revocation.is_jti_revoked_in_db", AsyncMock(return_value=False))
    pool.fetchrow = AsyncMock(return_value=None)
    with pytest.raises(HolonError) as disabled_principal:
        _run(deps.current_principal(request))
    assert disabled_principal.value.error_name == "PrincipalDisabled"
    pool.fetchrow = AsyncMock(return_value={"status": "active", "tenant_id": "acme"})
    monkeypatch.setattr(deps, "get_tenant", AsyncMock(return_value={"status": "disabled"}))
    with pytest.raises(HolonError) as tenant:
        _run(deps.current_principal(request))
    assert tenant.value.error_name == "TenantDisabled"

    assert callable(deps._validate_relation)
    with pytest.raises(AttributeError):
        deps.__getattr__("not_a_real_export")
    assert "_validate_relation" in deps.__dir__()

    Token = deps.TokenRequest
    assert Token(principal_urn="u", client_secret="s").principal_urn == "u"
    deps.AccessRequest(relation="viewer")
    deps.CreateTenantRequest(tenant_id="acme", display_name="Acme")
    deps.CreateWorkspaceRequest(workspace_id="main", display_name="Main", tenant_id="acme")
    deps.CreatePrincipalRequest(tenant_id="acme", type="user", local_name="ada", display_name="Ada", client_secret="long-secret")
    deps.StatusRequest(status="active")
    deps.GroupMemberRequest(principal_urn=principal.urn)
    deps.CreateProjectRequest(name="billing")


def test_access_ops_targets_status_and_member_fanout(monkeypatch) -> None:
    from app import access_ops

    actor = _principal()
    alice = _principal("hl:acme:global:user:alice")
    other = _principal("hl:other:global:user:bob", tenant="other")
    group = _principal("hl:acme:global:group:finance", type_="group")
    found = {alice.urn: alice, other.urn: other, group.urn: group}

    async def fetch(_pool, urn):
        return found.get(urn)

    monkeypatch.setattr(deps, "_fetch_principal", fetch)
    assert _run(access_ops._require_grant_target(alice.urn, tenant_id="acme")).urn == alice.urn
    with pytest.raises(HolonError) as missing:
        _run(access_ops._require_grant_target("hl:acme:global:user:missing", tenant_id="acme"))
    assert missing.value.status_code == 404
    with pytest.raises(HolonError) as cross:
        _run(access_ops._require_grant_target(other.urn, tenant_id="acme"))
    assert cross.value.error_name == "CrossTenantPrincipal"
    assert _run(access_ops._require_group(group.urn)).type == "group"
    with pytest.raises(HolonError):
        _run(access_ops._require_group("hl:acme:global:group:missing"))
    with pytest.raises(HolonError) as not_group:
        _run(access_ops._require_group(alice.urn))
    assert not_group.value.error_name == "NotAGroup"

    async def status_event(_pool, **kwargs):
        return {"status": kwargs["status"], "urn": kwargs["target_principal_urn"]}

    monkeypatch.setattr("app.status_events.enqueue_principal_status_event", status_event)
    updated = _run(access_ops._enqueue_principal_status_event(
        target_principal_urn=alice.urn, status="disabled", actor=actor, tenant_id="acme",
    ))
    assert updated["status"] == "disabled"

    deps.authz = MagicMock()
    deps.authz.read_relationships = AsyncMock(return_value=[{
        "relation": "member",
        "subject": {"object": {"objectType": "principal", "objectId": spicedb_object_id(alice.urn)}},
    }])
    deps.pool = MagicMock()
    deps.pool.fetch = AsyncMock(return_value=[{"urn": alice.urn, "display_name": "alice", "type": "user"}])
    enqueued: list[dict] = []

    async def capture(**kwargs):
        enqueued.append(kwargs)

    monkeypatch.setattr(access_ops, "_enqueue_permission_event", capture)
    _run(access_ops._fanout_group_permission_event(
        group,
        event_type="identity.permission.granted",
        resource_type="workspace",
        resource_urn="hl:acme:global:workspace:ops",
        relation="viewer",
        actor=actor,
        tenant_id="acme",
        workspace_id="ops",
    ))
    assert enqueued[0]["target_principal_urn"] == alice.urn
    assert enqueued[0]["workspace_id"] == "ops"


def test_federated_login_creates_links_and_rejects(monkeypatch) -> None:
    import asyncpg

    from app import federation_login

    audits: list[dict] = []
    monkeypatch.setattr(federation_login, "emit_audit", lambda **kwargs: audits.append(kwargs))
    deleted: list[dict] = []

    async def drop(**kwargs):
        deleted.append(kwargs)

    monkeypatch.setattr(federation_login, "_delete_relationship_or_reraise", drop)
    writes: list[dict] = []
    deps.authz = MagicMock()
    deps.authz.write_relationship = AsyncMock(side_effect=lambda **kwargs: writes.append(kwargs))

    row = {
        "urn": "hl:acme:global:user:ada",
        "type": "user",
        "tenant_id": "acme",
        "display_name": "Ada",
        "status": "active",
        "client_secret": None,
        "client_secret_hash": None,
        "oidc_sub": "sub-1",
        "external_id": None,
        "on_behalf_of": None,
        "country": None,
    }

    async def get_tenant(_pool, tenant_id):
        if tenant_id == "gone":
            return None
        if tenant_id == "off":
            return {"status": "disabled"}
        return {"status": "active"}

    monkeypatch.setattr(federation_login, "get_tenant", get_tenant)

    def _login(**overrides):
        payload = {
            "protocol": "oidc",
            "external_id": "sub-1",
            "tenant_id": "acme",
            "local_name": "ada",
            "display_name": "Ada",
            "workspace_roles": {},
            "frontend_redirect": "https://app/home",
        }
        payload.update(overrides)
        return federation_login._complete_federated_login(**payload)

    with pytest.raises(HolonError) as missing_tenant:
        _run(_login(tenant_id="gone"))
    assert missing_tenant.value.error_name == "TenantDisabled"
    with pytest.raises(HolonError):
        _run(_login(protocol="saml", tenant_id="off"))

    async def fetch_created(sql, *_args):
        if "oidc_sub" in sql:
            return None
        return row

    deps.pool = MagicMock()
    deps.pool.fetchrow = fetch_created

    async def insert(_pool, **kwargs):
        return {"urn": row["urn"], **kwargs}

    monkeypatch.setattr(federation_login, "insert_principal", insert)

    async def list_ws(_pool, _tenant_id):
        return [{"workspace_id": "old"}, {"workspace_id": "ops"}]

    async def get_ws(_pool, workspace_id):
        if workspace_id == "missing":
            return None
        if workspace_id == "foreign":
            return {"tenant_id": "other"}
        return {"tenant_id": "acme", "workspace_id": workspace_id}

    monkeypatch.setattr(federation_login, "list_workspaces", list_ws)
    monkeypatch.setattr(federation_login, "get_workspace", get_ws)
    redirect = _run(_login(workspace_roles={"ops": "admin", "missing": "viewer", "foreign": "editor"}))
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "https://app/home"
    assert any(item["relation"] == "member" for item in writes)
    assert any(item["relation"] == "admin" for item in writes)
    assert any(item["relation"] == "viewer" and item["resource_urn"].endswith(":workspace:old") for item in deleted)

    async def clash(_pool, **_kwargs):
        raise asyncpg.UniqueViolationError()

    monkeypatch.setattr(federation_login, "insert_principal", clash)
    deps.pool.fetchrow = AsyncMock(return_value=None)
    with pytest.raises(HolonError) as conflict:
        _run(_login(external_id="sub-2"))
    assert conflict.value.status_code == 409

    other = {**row, "tenant_id": "other", "urn": "hl:other:global:user:ada"}
    deps.pool.fetchrow = AsyncMock(return_value=other)
    with pytest.raises(HolonError) as mismatch:
        _run(_login(protocol="saml", external_id="ext-1"))
    assert mismatch.value.error_name == "SamlError"

    deps.pool.fetchrow = AsyncMock(return_value={**row, "status": "disabled"})
    with pytest.raises(HolonError) as disabled:
        _run(_login())
    assert disabled.value.error_name == "PrincipalDisabled"
    assert any(item["outcome"] == "success" for item in audits)
