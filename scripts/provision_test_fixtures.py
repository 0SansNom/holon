#!/usr/bin/env python3
"""CI / integration-test fixtures only — not a product demo.

Provisions principals, connector plugins, and ObjectTypes that the
pytest suite expects, via public APIs, after Identity's empty-instance
bootstrap. Never called from a service lifespan.

  make up && make provision-test-fixtures && make seed
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "libs"))

from holon_sdk import HolonClient


def _bootstrap_admin_secret() -> str:
    """No dev-login fallback any more — the bootstrap admin's real secret
    only lives in .env (this script runs on the host, not in a container,
    so it isn't necessarily in the invoking shell's own environment)."""
    value = os.environ.get("HOLON_BOOTSTRAP_ADMIN_SECRET")
    if value:
        return value
    env_path = REPO / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("HOLON_BOOTSTRAP_ADMIN_SECRET="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(
        "HOLON_BOOTSTRAP_ADMIN_SECRET not set (env or .env) — required to sign in as the bootstrap admin"
    )


IDENTITY = os.environ.get("HOLON_TEST_IDENTITY_URL", "http://localhost:8001")
CONNECTIVITY = os.environ.get("HOLON_TEST_CONNECTIVITY_URL", "http://localhost:8002")
KNOWLEDGE = os.environ.get("HOLON_TEST_KNOWLEDGE_URL", "http://localhost:8003")
TENANT_ID = os.environ.get("HOLON_TENANT_ID", "acme")
WORKSPACE_ID = os.environ.get("HOLON_WORKSPACE_ID", "main")
ADMIN_LOCAL = os.environ.get("HOLON_BOOTSTRAP_ADMIN_LOCAL_NAME", "admin")

client = HolonClient(identity_url=IDENTITY)

PLUGINS = [
    "holon_test_plugins.postgres_customers_plugin:PostgresCustomersPlugin",
    "holon_test_plugins.postgres_orders_plugin:PostgresOrdersPlugin",
    "holon_test_plugins.mongo_support_tickets_plugin:MongoSupportTicketsPlugin",
    "holon_test_plugins.reviews_rest_plugin:ReviewsRestPlugin",
    "holon_test_plugins.csv_suppliers_plugin:CsvSuppliersPlugin",
]

OBJECT_TYPES = [
    (
        "Customer", "customers",
        {"id": "id", "name": "name", "email": "email", "country": "country", "segment": "segment", "lifetimeValue": "lifetime_value", "updatedAt": "updated_at"},
        "name",
        {"id": "public", "name": "internal", "email": "confidential", "country": "internal", "segment": "internal", "lifetime_value": "confidential", "updated_at": "internal"},
    ),
    (
        "Order", "orders",
        {"id": "id", "customerId": "customer_id", "product": "product", "amount": "amount", "status": "status", "orderedAt": "ordered_at"},
        "id",
        {"id": "public", "customer_id": "internal", "product": "internal", "amount": "confidential", "status": "internal", "ordered_at": "internal"},
    ),
    (
        "SupportTicket", "support_tickets",
        {"id": "id", "customerId": "customer_id", "subject": "subject", "status": "status", "priority": "priority", "createdAt": "created_at"},
        "subject",
        {"id": "public", "customer_id": "internal", "subject": "internal", "status": "internal", "priority": "public", "created_at": "internal"},
    ),
    (
        "ProductReview", "reviews",
        {"id": "id", "orderId": "order_id", "rating": "rating", "comment": "comment", "reviewerName": "reviewer_name", "reviewedAt": "reviewed_at"},
        "id",
        {"id": "public", "order_id": "public", "rating": "public", "comment": "public", "reviewer_name": "public", "reviewed_at": "public"},
    ),
    (
        "Supplier", "suppliers",
        {"id": "id", "name": "name", "country": "country", "category": "category"},
        "name",
        {"id": "public", "name": "internal", "country": "internal", "category": "internal"},
    ),
    (
        "InventoryLevel", "inventory_levels",
        {"id": "id", "warehouse": "warehouse", "quantity": "quantity", "updatedAt": "updated_at"},
        "id",
        {"id": "public", "warehouse": "internal", "quantity": "internal", "updated_at": "internal"},
    ),
]

RELATION_TYPES = [
    {
        "name": "Order.customer",
        "source_object_type": "Order",
        "target_object_type": "Customer",
        "source_property": "customerId",
        "target_property": "id",
        "cardinality": "many_to_one",
        "storage_kind": "foreign_key",
        "source_api_name": "customer",
        "target_api_name": "orders",
        "lifecycle_status": "active",
    },
    {
        "name": "SupportTicket.customer",
        "source_object_type": "SupportTicket",
        "target_object_type": "Customer",
        "source_property": "customerId",
        "target_property": "id",
        "cardinality": "many_to_one",
        "storage_kind": "foreign_key",
        "source_api_name": "customer",
        "target_api_name": "tickets",
        "lifecycle_status": "active",
    },
    {
        "name": "ProductReview.order",
        "source_object_type": "ProductReview",
        "target_object_type": "Order",
        "source_property": "orderId",
        "target_property": "id",
        "cardinality": "many_to_one",
        "storage_kind": "foreign_key",
        "source_api_name": "order",
        "target_api_name": "reviews",
        "lifecycle_status": "active",
    },
]

# Reproduces the two former hardcoded Customer Actions as declarative Action
# Types — `credit_hold_reason`/`account_closed_reason` source from the
# invocation's own `reason` field (always available as an implicit
# `parameter_name: "reason"` edit source, see declarative.py).
ACTION_TYPES = [
    {
        "name": "Customer.putOnCreditHold",
        "target_object_type": "Customer",
        "required_permission": "write",
        "risk_level": "low",
        "description": "Places a Customer's account on credit hold, recording a reason. "
        "Applies immediately (low risk — reversible, no external write, no deletion).",
        "edits": [
            {"property": "credit_hold", "source": "literal", "value": True},
            {"property": "credit_hold_reason", "source": "parameter", "parameter_name": "reason"},
        ],
        "function_side_effect": "lifetime_tier",
    },
    {
        "name": "Customer.closeAccount",
        "target_object_type": "Customer",
        "required_permission": "write",
        "risk_level": "high",
        "description": "Closes a Customer's account. Proposes a human-in-the-loop approval "
        "request (high risk — deletion-class, writes back to source system).",
        "edits": [
            {"property": "account_closed", "source": "literal", "value": True},
            {"property": "account_closed_reason", "source": "parameter", "parameter_name": "reason"},
        ],
        "writeback_dataset": "customers",
    },
]

GLOSSARY_TERMS = [
    ("client", "A business account that buys from us — see ObjectType Customer.", ["customer", "compte client"], "Customer"),
    ("grand compte", "A Customer in the 'enterprise' commercial segment — our highest-value tier.", ["enterprise customer", "grand client"], "Customer"),
    ("encours", "A Customer's lifetime value — total historical spend, in euros.", ["lifetime value", "valeur client"], "Customer"),
    ("mise en attente de crédit", "The Customer.putOnCreditHold Action — blocks further orders pending payment resolution.", ["credit hold", "blocage crédit"], "Customer"),
    ("clôture de compte", "The Customer.closeAccount Action — permanently closes an account. High-risk, requires approval.", ["account closure", "fermeture de compte"], "Customer"),
    ("commande", "A single purchase placed by a Customer — see ObjectType Order.", ["order", "achat"], "Order"),
    ("ticket", "A customer support request — see ObjectType SupportTicket.", ["support ticket", "demande d'assistance"], "SupportTicket"),
    ("avis produit", "A public review left against an Order — see ObjectType ProductReview.", ["product review", "évaluation"], "ProductReview"),
    ("fournisseur", "A vendor we source materials/components from — see ObjectType Supplier.", ["supplier", "vendeur"], "Supplier"),
    ("niveau de stock", "The current on-hand quantity of a SKU at a warehouse — see ObjectType InventoryLevel.", ["inventory level", "stock disponible"], "InventoryLevel"),
]

PERSONAS = [
    ("jdoe", "user", "Jane Doe", "FR", None, "editor"),
    ("msmith", "user", "Mary Smith", "DE", None, "admin"),
    ("kenji", "user", "Kenji Sato", "JP", None, "viewer"),
    ("alice", "user", "Alice TenantMember", "FR", None, None),
    ("ingest-bot", "agent", "Ingest Bot", "FR", f"hl:{TENANT_ID}:global:user:jdoe", "viewer"),
    ("connectivity-connector", "service_account", "Connectivity Connector", None, None, "editor"),
    ("connectivity-pipeline-runner", "service_account", "Connectivity Pipeline Runner", None, None, "editor"),
    ("automation-workflow-engine", "service_account", "Automation Workflow Engine", None, None, "editor"),
    ("automation-agent-chain-trigger", "service_account", "Automation Agent Chain Trigger", None, None, "viewer"),
    ("knowledge-model-caller", "service_account", "Knowledge Model Caller", None, None, "viewer"),
    ("knowledge-project-validator", "service_account", "Knowledge Project Validator", None, None, "viewer"),
]


def _wait(url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _ = client.request("GET", f"{url}/health")
            if status == 200:
                return
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(1.5)
    raise TimeoutError(f"{url} never became healthy")


def _urn(type_segment: str, local: str) -> str:
    return f"hl:{TENANT_ID}:global:{type_segment}:{local}"


def main() -> None:
    print("Waiting for Identity / Connectivity / Knowledge…")
    for url in (IDENTITY, CONNECTIVITY, KNOWLEDGE):
        _wait(url)

    admin_urn = _urn("user", ADMIN_LOCAL)
    print(f"Signing in as bootstrap admin {admin_urn}…")
    admin_token = client.token_for(admin_urn, client_secret=_bootstrap_admin_secret())

    for local, ptype, display, country, on_behalf, relation in PERSONAS:
        type_seg = "service-account" if ptype == "service_account" else ptype
        urn = _urn(type_seg, local)
        body = {
            "tenant_id": TENANT_ID,
            "type": ptype,
            "local_name": local,
            "display_name": display,
            "country": country,
            "on_behalf_of": on_behalf,
            # Explicit test-fixture convention — the pytest suite mints
            # tokens with exactly this secret; nothing derives it any more.
            "client_secret": f"{local}-dev-secret",
        }
        status, created = client.request("POST", f"{IDENTITY}/principals", token=admin_token, body=body)
        if status not in (201, 409):
            raise SystemExit(f"create principal {local}: {status} {created}")
        print(f"  principal {local}: {status}")
        if relation:
            status, granted = client.request(
                "POST",
                f"{IDENTITY}/principals/{urn}/access/grant",
                token=admin_token,
                body={"relation": relation, "workspace_id": WORKSPACE_ID},
            )
            if status not in (200, 201):
                raise SystemExit(f"grant {local} {relation}: {status} {granted}")
            print(f"  grant {local} → {relation}")

    try:
        editor_token = client.token_for(_urn("user", "jdoe"), client_secret="jdoe-dev-secret")
    except TimeoutError:
        editor_token = admin_token

    for entry in PLUGINS:
        status, body = client.request(
            "POST", f"{CONNECTIVITY}/plugins", token=editor_token, body={"entry_point": entry}
        )
        if status not in (200, 201, 409):
            raise SystemExit(f"register plugin {entry}: {status} {body}")
        print(f"  plugin {entry.split(':')[-1]}: {status}")

    status, body = client.request(
        "POST",
        f"{CONNECTIVITY}/kafka-streams",
        token=editor_token,
        body={
            "name": "inventory-levels-stream",
            "topic": "external-inventory-stream",
            "key_field": "sku",
            "dataset_name": "inventory_levels",
            "batch_interval_seconds": 5,
        },
    )
    if status not in (201, 409):
        raise SystemExit(f"kafka-stream inventory-levels-stream: {status} {body}")
    print(f"  kafka-stream inventory-levels-stream: {status}")

    status, body = client.request(
        "POST",
        f"{CONNECTIVITY}/write-targets",
        token=editor_token,
        body={
            "dataset_name": "customers",
            "table_name": "customers",
            "id_column": "id",
            "allowed_properties": {"account_closed": "account_closed"},
        },
    )
    if status not in (200, 201, 409):
        raise SystemExit(f"write-target customers: {status} {body}")

    admin_or_msmith = client.token_for(_urn("user", "msmith"), client_secret="msmith-dev-secret")
    for name, dataset, mapping, title_key, column_classification in OBJECT_TYPES:
        dataset_urn = f"hl:{TENANT_ID}:{WORKSPACE_ID}:dataset:{dataset}"
        status, body = client.request(
            "POST",
            f"{KNOWLEDGE}/api/holon/object-types",
            token=admin_or_msmith,
            body={
                "name": name,
                "source_dataset_urn": dataset_urn,
                "property_mapping": mapping,
                "title_key": title_key,
                "column_classification": column_classification,
                "lifecycle_status": "active",
                "description": f"Test fixture ObjectType {name}",
            },
        )
        if status not in (201, 409):
            raise SystemExit(f"object-type {name}: {status} {body}")
        print(f"  object-type {name}: {status}")

    for relation_type in RELATION_TYPES:
        status, body = client.request(
            "POST", f"{KNOWLEDGE}/api/holon/relation-types", token=admin_or_msmith, body=relation_type
        )
        if status not in (201, 409):
            raise SystemExit(f"relation-type {relation_type['name']}: {status} {body}")
        print(f"  relation-type {relation_type['name']}: {status}")

    for action_type in ACTION_TYPES:
        status, body = client.request(
            "POST", f"{KNOWLEDGE}/api/holon/action-types", token=admin_or_msmith, body=action_type
        )
        if status not in (201, 409):
            raise SystemExit(f"action-type {action_type['name']}: {status} {body}")
        print(f"  action-type {action_type['name']}: {status}")

    for term, definition, synonyms, related_object_type in GLOSSARY_TERMS:
        status, body = client.request(
            "POST",
            f"{KNOWLEDGE}/api/holon/glossary",
            token=admin_or_msmith,
            body={"term": term, "definition": definition, "synonyms": synonyms, "related_object_type": related_object_type},
        )
        if status not in (201, 409):
            raise SystemExit(f"glossary term {term}: {status} {body}")
        print(f"  glossary term {term}: {status}")

    status, body = client.request(
        "POST",
        f"{KNOWLEDGE}/api/holon/function-plugins",
        token=admin_or_msmith,
        body={"entry_point": "holon_test_plugins.lifetime_tier_function:LifetimeTierFunction"},
    )
    if status not in (200, 201, 409):
        raise SystemExit(f"function-plugin lifetime_tier: {status} {body}")
    print(f"  function-plugin lifetime_tier: {status}")

    print("\nTest fixtures provisioned.")


if __name__ == "__main__":
    main()
