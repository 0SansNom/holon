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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))

from holon_sdk import HolonClient  # noqa: E402

IDENTITY = os.environ.get("HOLON_TEST_IDENTITY_URL", "http://localhost:8001")
CONNECTIVITY = os.environ.get("HOLON_TEST_CONNECTIVITY_URL", "http://localhost:8002")
KNOWLEDGE = os.environ.get("HOLON_TEST_KNOWLEDGE_URL", "http://localhost:8003")
TENANT_ID = os.environ.get("HOLON_TENANT_ID", "acme")
WORKSPACE_ID = os.environ.get("HOLON_WORKSPACE_ID", "main")
ADMIN_LOCAL = os.environ.get("HOLON_BOOTSTRAP_ADMIN_LOCAL_NAME", "admin")

client = HolonClient(identity_url=IDENTITY)

PLUGINS = [
    "app.plugins.postgres_customers_plugin:PostgresCustomersPlugin",
    "app.plugins.postgres_orders_plugin:PostgresOrdersPlugin",
    "app.plugins.mongo_support_tickets_plugin:MongoSupportTicketsPlugin",
    "app.plugins.reviews_rest_plugin:ReviewsRestPlugin",
    "app.plugins.csv_suppliers_plugin:CsvSuppliersPlugin",
]

OBJECT_TYPES = [
    ("Customer", "customers", {"id": "id", "name": "name", "email": "email", "country": "country", "segment": "segment", "lifetimeValue": "lifetime_value", "updatedAt": "updated_at"}, "name"),
    ("Order", "orders", {"id": "id", "customerId": "customer_id", "product": "product", "amount": "amount", "status": "status", "orderedAt": "ordered_at"}, "id"),
    ("SupportTicket", "support_tickets", {"id": "id", "customerId": "customer_id", "subject": "subject", "status": "status", "priority": "priority", "createdAt": "created_at"}, "subject"),
    ("ProductReview", "reviews", {"id": "id", "orderId": "order_id", "rating": "rating", "comment": "comment", "reviewerName": "reviewer_name", "reviewedAt": "reviewed_at"}, "id"),
    ("Supplier", "suppliers", {"id": "id", "name": "name", "country": "country", "category": "category"}, "name"),
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
    admin_token = client.token_for(admin_urn)

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
        editor_token = client.token_for(_urn("user", "jdoe"))
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

    admin_or_msmith = client.token_for(_urn("user", "msmith"))
    for name, dataset, mapping, title_key in OBJECT_TYPES:
        dataset_urn = f"hl:{TENANT_ID}:{WORKSPACE_ID}:dataset:{dataset}"
        status, body = client.request(
            "POST",
            f"{KNOWLEDGE}/object-types",
            token=admin_or_msmith,
            body={
                "name": name,
                "source_dataset_urn": dataset_urn,
                "property_mapping": mapping,
                "title_key": title_key,
                "lifecycle_status": "active",
                "description": f"Test fixture ObjectType {name}",
            },
        )
        if status not in (201, 409):
            raise SystemExit(f"object-type {name}: {status} {body}")
        print(f"  object-type {name}: {status}")

    print("\nTest fixtures provisioned.")


if __name__ == "__main__":
    main()
