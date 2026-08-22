#!/usr/bin/env python3
"""holon — a minimal CLI wrapping Holon's REST APIs.

"Foundry's dev-toolchain story at
a basic, honest level, not a full SDK ecosystem." This wraps the core
read/governance surface across services (auth, principal/workspace/
project management, ontology, objects, Actions, Applications, Pipelines,
Model predictions) — genuinely broad, deliberately shallow: no attempt
to cover every endpoint this build now has (Markings, Analytics's
group_by/join, execution replay, and more all stay API-only), the same
"minimal" scope the plan itself calls for.

Standard library only — any Python 3.9+, no extra install. Talks to the same fixed
localhost ports every other client in this build already uses
(this project's own test suite, the web SPA's `api/config.ts`) — no
env-var indirection for a single-tenant local deployment, overridable via
HOLON_CLI_*_URL only if you genuinely need to point elsewhere.

Session state (a bearer token, minted by `holon login`) is cached in
~/.holon/session.json between invocations — plain-text, dev-only.
Tokens expire in an hour (Identity's own `issue_token` default);
re-run `holon login` when a command starts failing with 401.

Usage:
    holon login jdoe --client-secret "$JDoe_SECRET"   # or HOLON_CLIENT_SECRET
    holon whoami
    holon principals list
    holon projects list
    holon projects create my-project
    holon projects grant my-project hl:acme:global:user:kenji viewer
    holon workspace grant hl:acme:global:user:kenji editor
    holon ontology list
    holon ontology get Customer
    holon objects list Customer
    holon objects get Customer 1
    holon actions list
    holon actions invoke Customer.putOnCreditHold 1 --reason "past due"
    holon applications list
    holon applications promote my-app
    holon pipelines run my-pipeline
    holon models predict customer-value-classifier --features '{"lifetimeValue": 184500}'
    holon codegen python ./generated
    holon codegen typescript ./generated
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

IDENTITY_URL = os.environ.get("HOLON_CLI_IDENTITY_URL", "http://localhost:8001")
CONNECTIVITY_URL = os.environ.get("HOLON_CLI_CONNECTIVITY_URL", "http://localhost:8002")
KNOWLEDGE_URL = os.environ.get("HOLON_CLI_KNOWLEDGE_URL", "http://localhost:8003")
WORKSPACE_ID = os.environ.get("HOLON_CLI_WORKSPACE_ID", "main")


def _ontology(path: str) -> str:
    return f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}{path}"


def _holon(path: str) -> str:
    return f"{KNOWLEDGE_URL}/api/holon{path}"
EXPERIENCE_URL = os.environ.get("HOLON_CLI_EXPERIENCE_URL", "http://localhost:8004")
INTELLIGENCE_URL = os.environ.get("HOLON_CLI_INTELLIGENCE_URL", "http://localhost:8006")

TENANT_ID = os.environ.get("HOLON_CLI_TENANT_ID", "acme")
SESSION_PATH = Path.home() / ".holon" / "session.json"


def _client_secret(args: argparse.Namespace) -> str:
    secret = args.client_secret or os.environ.get("HOLON_CLIENT_SECRET", "").strip()
    if not secret:
        print(
            "error: a client secret is required — pass --client-secret or set HOLON_CLIENT_SECRET "
            "(it is returned once by POST /principals at principal creation)",
            file=sys.stderr,
        )
        sys.exit(1)
    return secret


def _request(method: str, url: str, *, token: Optional[str] = None, body: Optional[dict] = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"detail": raw.decode(errors="replace")}
    except urllib.error.URLError as exc:
        print(f"error: could not reach {url}: {exc.reason}", file=sys.stderr)
        sys.exit(1)


def _load_session() -> Optional[dict]:
    if not SESSION_PATH.exists():
        return None
    return json.loads(SESSION_PATH.read_text())


def _save_session(session: dict) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(json.dumps(session))
    SESSION_PATH.chmod(0o600)


def _require_token() -> str:
    session = _load_session()
    if session is None:
        print("error: not logged in — run `holon login <local-name>` first", file=sys.stderr)
        sys.exit(1)
    return session["access_token"]


def _print_json(body: Any) -> None:
    print(json.dumps(body, indent=2))


def _die_on_error(status: int, body: Any) -> None:
    if status >= 400:
        detail = body.get("detail", body) if isinstance(body, dict) else body
        print(f"error ({status}): {detail}", file=sys.stderr)
        sys.exit(1)


def _call(method: str, url: str, *, token: str, body: Optional[dict] = None) -> Any:
    status, response_body = _request(method, url, token=token, body=body)
    _die_on_error(status, response_body)
    return response_body


# --- commands ---


def cmd_login(args: argparse.Namespace) -> None:
    urn = f"hl:{TENANT_ID}:global:user:{args.local_name}"
    status, body = _request(
        "POST", f"{IDENTITY_URL}/token", body={"principal_urn": urn, "client_secret": _client_secret(args)}
    )
    _die_on_error(status, body)
    _save_session({"principal_urn": urn, "access_token": body["access_token"]})
    print(f"logged in as {urn}")


def cmd_whoami(args: argparse.Namespace) -> None:
    _print_json(_call("GET", f"{IDENTITY_URL}/whoami", token=_require_token()))


def cmd_principals_list(args: argparse.Namespace) -> None:
    _print_json(_call("GET", f"{IDENTITY_URL}/principals", token=_require_token()))


def cmd_projects_list(args: argparse.Namespace) -> None:
    _print_json(_call("GET", f"{IDENTITY_URL}/projects", token=_require_token()))


def cmd_projects_create(args: argparse.Namespace) -> None:
    _print_json(_call("POST", f"{IDENTITY_URL}/projects", token=_require_token(), body={"name": args.name}))


def cmd_projects_grant(args: argparse.Namespace) -> None:
    url = f"{IDENTITY_URL}/projects/{args.name}/principals/{args.principal_urn}/access/grant"
    _print_json(_call("POST", url, token=_require_token(), body={"relation": args.relation}))


def cmd_projects_revoke(args: argparse.Namespace) -> None:
    url = f"{IDENTITY_URL}/projects/{args.name}/principals/{args.principal_urn}/access/revoke"
    _print_json(_call("POST", url, token=_require_token(), body={"relation": args.relation}))


def cmd_workspace_grant(args: argparse.Namespace) -> None:
    url = f"{IDENTITY_URL}/principals/{args.principal_urn}/access/grant"
    _print_json(_call("POST", url, token=_require_token(), body={"relation": args.relation}))


def cmd_workspace_revoke(args: argparse.Namespace) -> None:
    url = f"{IDENTITY_URL}/principals/{args.principal_urn}/access/revoke"
    _print_json(_call("POST", url, token=_require_token(), body={"relation": args.relation}))


def cmd_ontology_list(args: argparse.Namespace) -> None:
    _print_json(_call("GET", _ontology("/objectTypes"), token=_require_token()))


def cmd_ontology_get(args: argparse.Namespace) -> None:
    _print_json(_call("GET", _ontology(f"/objectTypes/{args.name}"), token=_require_token()))


def cmd_objects_list(args: argparse.Namespace) -> None:
    _print_json(_call("GET", _ontology(f"/objects/{args.object_type}"), token=_require_token()))


def cmd_objects_get(args: argparse.Namespace) -> None:
    _print_json(_call("GET", _ontology(f"/objects/{args.object_type}/{args.id}"), token=_require_token()))


def cmd_actions_list(args: argparse.Namespace) -> None:
    _print_json(_call("GET", _holon("/actions"), token=_require_token()))


def cmd_actions_invoke(args: argparse.Namespace) -> None:
    if "." not in args.action:
        print("error: action must be 'ObjectType.actionName', e.g. Customer.putOnCreditHold", file=sys.stderr)
        sys.exit(1)
    object_type, local_action = args.action.split(".", 1)
    url = _ontology(f"/objects/{object_type}/{args.id}/actions/{local_action}")
    _print_json(_call("POST", url, token=_require_token(), body={"reason": args.reason}))


def cmd_applications_list(args: argparse.Namespace) -> None:
    _print_json(_call("GET", f"{EXPERIENCE_URL}/api/applications", token=_require_token()))


def cmd_applications_get(args: argparse.Namespace) -> None:
    _print_json(_call("GET", f"{EXPERIENCE_URL}/api/applications/{args.name}", token=_require_token()))


def cmd_applications_promote(args: argparse.Namespace) -> None:
    _print_json(_call("POST", f"{EXPERIENCE_URL}/api/applications/{args.name}/promote", token=_require_token()))


def cmd_pipelines_run(args: argparse.Namespace) -> None:
    _print_json(_call("POST", f"{CONNECTIVITY_URL}/pipelines/{args.name}/run", token=_require_token()))


def cmd_models_predict(args: argparse.Namespace) -> None:
    try:
        features = json.loads(args.features)
    except json.JSONDecodeError as exc:
        print(f"error: --features must be valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    url = f"{INTELLIGENCE_URL}/models/{args.name}/predict"
    _print_json(_call("POST", url, token=_require_token(), body={"features": features}))


def cmd_codegen(args: argparse.Namespace) -> None:
    """The OSDK: `holon_osdk`/`holon_sdk` are stdlib-only, matching this
    CLI's own convention, so importing them here doesn't add a real
    third-party dependency — the `libs/` path just isn't on `sys.path`
    by default for a bare `python3 cli/holon.py` invocation, the same
    reason every `tests/` file inserts it too.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))
    from holon_osdk import emit_python, emit_typescript, fetch_schema

    schema = fetch_schema(knowledge_url=KNOWLEDGE_URL, token=_require_token())
    if args.language == "python":
        output = emit_python(schema)
        filename = "holon_ontology.py"
    else:
        output = emit_typescript(schema)
        filename = "holon_ontology.ts"

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    output_path.write_text(output)
    print(f"wrote {output_path} ({len(schema.object_types)} ObjectType(s), {len(schema.action_types)} Action(s))")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="holon", description="A minimal CLI wrapping Holon's REST APIs.")
    top = parser.add_subparsers(dest="command", required=True)

    login = top.add_parser("login", help="Mint and cache a session token for a principal")
    login.add_argument("local_name", help="e.g. jdoe, msmith, kenji, alice")
    login.add_argument(
        "--client-secret",
        default=None,
        help="the principal's client secret (or set HOLON_CLIENT_SECRET)",
    )
    login.set_defaults(func=cmd_login)

    top.add_parser("whoami", help="Show the current session's principal").set_defaults(func=cmd_whoami)

    principals = top.add_parser("principals", help="Principal management").add_subparsers(dest="subcommand", required=True)
    principals.add_parser("list", help="List all principals").set_defaults(func=cmd_principals_list)

    projects = top.add_parser("projects", help="Project management").add_subparsers(dest="subcommand", required=True)
    projects.add_parser("list", help="List projects").set_defaults(func=cmd_projects_list)
    p_create = projects.add_parser("create", help="Create a project")
    p_create.add_argument("name")
    p_create.set_defaults(func=cmd_projects_create)
    p_grant = projects.add_parser("grant", help="Grant a principal access to a project")
    p_grant.add_argument("name")
    p_grant.add_argument("principal_urn")
    p_grant.add_argument("relation", choices=["viewer", "editor", "admin"])
    p_grant.set_defaults(func=cmd_projects_grant)
    p_revoke = projects.add_parser("revoke", help="Revoke a principal's project access")
    p_revoke.add_argument("name")
    p_revoke.add_argument("principal_urn")
    p_revoke.add_argument("relation", choices=["viewer", "editor", "admin"])
    p_revoke.set_defaults(func=cmd_projects_revoke)

    workspace = top.add_parser("workspace", help="Workspace-level access management").add_subparsers(dest="subcommand", required=True)
    w_grant = workspace.add_parser("grant", help="Grant a principal workspace access")
    w_grant.add_argument("principal_urn")
    w_grant.add_argument("relation", choices=["viewer", "editor", "admin"])
    w_grant.set_defaults(func=cmd_workspace_grant)
    w_revoke = workspace.add_parser("revoke", help="Revoke a principal's workspace access")
    w_revoke.add_argument("principal_urn")
    w_revoke.add_argument("relation", choices=["viewer", "editor", "admin"])
    w_revoke.set_defaults(func=cmd_workspace_revoke)

    ontology = top.add_parser("ontology", help="Ontology inspection").add_subparsers(dest="subcommand", required=True)
    ontology.add_parser("list", help="List ObjectTypes").set_defaults(func=cmd_ontology_list)
    o_get = ontology.add_parser("get", help="Get an ObjectType's live definition")
    o_get.add_argument("name")
    o_get.set_defaults(func=cmd_ontology_get)

    objects = top.add_parser("objects", help="Object instance reads (PDP-gated)").add_subparsers(dest="subcommand", required=True)
    obj_list = objects.add_parser("list", help="List instances of an ObjectType")
    obj_list.add_argument("object_type")
    obj_list.set_defaults(func=cmd_objects_list)
    obj_get = objects.add_parser("get", help="Get one instance by id")
    obj_get.add_argument("object_type")
    obj_get.add_argument("id")
    obj_get.set_defaults(func=cmd_objects_get)

    actions = top.add_parser("actions", help="Ontology Actions").add_subparsers(dest="subcommand", required=True)
    actions.add_parser("list", help="List declared Actions").set_defaults(func=cmd_actions_list)
    act_invoke = actions.add_parser("invoke", help="Invoke an Action, e.g. Customer.putOnCreditHold")
    act_invoke.add_argument("action")
    act_invoke.add_argument("id")
    act_invoke.add_argument("--reason", required=True)
    act_invoke.set_defaults(func=cmd_actions_invoke)

    applications = top.add_parser("applications", help="Application Builder").add_subparsers(dest="subcommand", required=True)
    applications.add_parser("list", help="List applications").set_defaults(func=cmd_applications_list)
    app_get = applications.add_parser("get", help="Get an application's definition")
    app_get.add_argument("name")
    app_get.set_defaults(func=cmd_applications_get)
    app_promote = applications.add_parser("promote", help="Promote an application's current draft")
    app_promote.add_argument("name")
    app_promote.set_defaults(func=cmd_applications_promote)

    pipelines = top.add_parser("pipelines", help="Pipeline / Transform DAG").add_subparsers(dest="subcommand", required=True)
    pl_run = pipelines.add_parser("run", help="Run a registered pipeline")
    pl_run.add_argument("name")
    pl_run.set_defaults(func=cmd_pipelines_run)

    models = top.add_parser("models", help="Model Integration").add_subparsers(dest="subcommand", required=True)
    m_predict = models.add_parser("predict", help="Run a prediction against a registered model")
    m_predict.add_argument("name")
    m_predict.add_argument("--features", required=True, help='JSON object, e.g. \'{"lifetimeValue": 184500}\'')
    m_predict.set_defaults(func=cmd_models_predict)

    codegen = top.add_parser("codegen", help="Generate a typed client from the live ontology (the OSDK)")
    codegen.add_argument("language", choices=["python", "typescript"])
    codegen.add_argument("output_dir")
    codegen.set_defaults(func=cmd_codegen)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
