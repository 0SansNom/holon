#!/usr/bin/env python3
"""Purge pytest leftover Ontology / apps / sources from local Holon Postgres.

Matches the Experience UI heuristic in
`services/experience/web/src/components/Ontology/ephemeralResources.ts`.

Usage (stack must be up via docker compose):

  python3 scripts/cleanup_pytest_leftovers.py          # dry-run
  python3 scripts/cleanup_pytest_leftovers.py --apply  # delete

Never touches durable fixture ObjectTypes: Customer, Order, Supplier,
ProductReview, SupportTicket, InventoryLevel (and their seeded datasets).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

DURABLE_OBJECT_TYPES = (
    "Customer",
    "Order",
    "Supplier",
    "ProductReview",
    "SupportTicket",
    "InventoryLevel",
)

# SQL boolean expression: {n} is a text expression (column or literal).
# Keep in sync with services/experience/web/.../ephemeralResources.ts
EPHEMERAL_SQL = r"""(
  {n} ~* '^test-'
  OR {n} ~ '_\d{{10,}}$'
  OR {n} ~* '-[0-9a-f]{{6,10}}$'
  OR {n} ~ '^[A-Z][A-Za-z0-9]*[0-9a-f]{{6,8}}$'
  OR {n} ~ '^[a-z][a-z0-9_]*[0-9a-f]{{6,8}}$'
  OR split_part({n}, '.', 1) ~ '_\d{{10,}}$'
  OR split_part({n}, '.', 1) ~ '^[A-Z][A-Za-z0-9]*[0-9a-f]{{6,8}}$'
  OR split_part({n}, '.', 1) ~ '^[a-z][a-z0-9_]*[0-9a-f]{{6,8}}$'
  OR (
    position('.' in {n}) > 0
    AND split_part({n}, '.', 2) ~ '^[a-z][a-z0-9_]*[0-9a-f]{{6,8}}$'
  )
)"""


def ephemeral(n: str) -> str:
    return EPHEMERAL_SQL.format(n=n)


def postgres_password() -> str:
    env_path = REPO / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("POSTGRES_PASSWORD="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("POSTGRES_PASSWORD", "change-me")


def psql(database: str, sql: str, *, tuples_only: bool = False) -> str:
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "-e",
        f"PGPASSWORD={postgres_password()}",
        "postgres",
        "psql",
        "-U",
        "holon",
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    if tuples_only:
        cmd += ["-t", "-A"]
    cmd += ["-c", sql]
    proc = subprocess.run(
        cmd,
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout)
        raise SystemExit(proc.returncode or 1)
    return proc.stdout


def count_sql(database: str, label: str, sql: str) -> tuple[str, int]:
    out = psql(database, sql, tuples_only=True).strip()
    try:
        return label, int(out or "0")
    except ValueError:
        return label, 0


def inventory() -> list[tuple[str, int]]:
    e_name = ephemeral("name")
    e_api = ephemeral("api_name")
    e_ot = ephemeral("object_type")
    e_action = ephemeral("action_name")
    durable = ", ".join(f"'{n}'" for n in DURABLE_OBJECT_TYPES)

    rows = [
        count_sql(
            "holon_knowledge",
            "object_type",
            f"SELECT count(*) FROM object_type WHERE ({e_name}) AND name NOT IN ({durable})",
        ),
        count_sql(
            "holon_knowledge",
            "interface_type",
            f"SELECT count(*) FROM interface_type WHERE {e_name}",
        ),
        count_sql(
            "holon_knowledge",
            "value_type",
            f"SELECT count(*) FROM value_type WHERE {e_name}",
        ),
        count_sql(
            "holon_knowledge",
            "shared_property_type",
            f"SELECT count(*) FROM shared_property_type WHERE {e_api}",
        ),
        count_sql(
            "holon_knowledge",
            "relation_type",
            f"SELECT count(*) FROM relation_type WHERE {e_name}",
        ),
        count_sql(
            "holon_knowledge",
            "action_type",
            f"SELECT count(*) FROM action_type WHERE {e_name}",
        ),
        count_sql(
            "holon_knowledge",
            "object_set",
            f"SELECT count(*) FROM object_set WHERE {e_name}",
        ),
        count_sql(
            "holon_knowledge",
            "object_instance (ephemeral OT)",
            f"SELECT count(*) FROM object_instance WHERE ({e_ot}) AND object_type NOT IN ({durable})",
        ),
        count_sql(
            "holon_experience",
            "application",
            f"SELECT count(*) FROM application WHERE {e_name}",
        ),
        count_sql(
            "holon_connectivity",
            "generic_rest_source",
            f"SELECT count(*) FROM generic_rest_source WHERE {e_name}",
        ),
        count_sql(
            "holon_connectivity",
            "pipeline_definition",
            f"SELECT count(*) FROM pipeline_definition WHERE {e_name}",
        ),
    ]
    # Approvals / invocations for ephemeral actions (best-effort)
    rows.append(
        count_sql(
            "holon_knowledge",
            "action_approval (ephemeral action)",
            f"SELECT count(*) FROM action_approval WHERE {e_action}",
        )
    )
    rows.append(
        count_sql(
            "holon_knowledge",
            "action_invocation (ephemeral action)",
            f"SELECT count(*) FROM action_invocation WHERE {e_action}",
        )
    )
    return rows


def apply_cleanup() -> None:
    e_name = ephemeral("name")
    e_api = ephemeral("api_name")
    e_ot = ephemeral("object_type")
    e_action = ephemeral("action_name")
    durable = ", ".join(f"'{n}'" for n in DURABLE_OBJECT_TYPES)

    knowledge_sql = f"""
BEGIN;

CREATE TEMP TABLE ephemeral_ot AS
SELECT urn, name FROM object_type
WHERE ({ephemeral("name")}) AND name NOT IN ({durable});

CREATE TEMP TABLE ephemeral_ot_names AS SELECT name FROM ephemeral_ot;

-- Instances for ephemeral ObjectTypes
DELETE FROM object_instance_edit
 WHERE object_type IN (SELECT name FROM ephemeral_ot_names);
DELETE FROM object_instance_history
 WHERE object_type IN (SELECT name FROM ephemeral_ot_names);
DELETE FROM object_instance_tombstone
 WHERE object_type IN (SELECT name FROM ephemeral_ot_names);
DELETE FROM object_instance
 WHERE object_type IN (SELECT name FROM ephemeral_ot_names);

-- Action telemetry for ephemeral actions / instances
DELETE FROM action_approval WHERE {e_action};
DELETE FROM action_invocation WHERE {e_action};
DELETE FROM action_approval
 WHERE instance_urn LIKE ANY (ARRAY(SELECT '%:' || name || '/%' FROM ephemeral_ot_names));
DELETE FROM action_invocation
 WHERE instance_urn LIKE ANY (ARRAY(SELECT '%:' || name || '/%' FROM ephemeral_ot_names));

-- Object sets bound to ephemeral types or ephemeral names
DELETE FROM object_set
 WHERE {e_name}
    OR object_type_urn IN (SELECT urn FROM ephemeral_ot);

-- Actions whose name is ephemeral OR target is ephemeral OT
DELETE FROM action_type
 WHERE {e_name}
    OR target_object_type IN (SELECT name FROM ephemeral_ot_names);

-- RelationTypes named ephemerally or pointing at ephemeral OTs
DELETE FROM relation_link_overlay
 WHERE relation_urn IN (
   SELECT urn FROM relation_type
   WHERE {e_name}
      OR source_object_type_urn IN (SELECT urn FROM ephemeral_ot)
      OR target_object_type_urn IN (SELECT urn FROM ephemeral_ot)
 );
DELETE FROM relation_type
 WHERE {e_name}
    OR source_object_type_urn IN (SELECT urn FROM ephemeral_ot)
    OR target_object_type_urn IN (SELECT urn FROM ephemeral_ot);

-- Lineage edges involving ephemeral OT URNs
DELETE FROM lineage_edge
 WHERE source_urn IN (SELECT urn FROM ephemeral_ot)
    OR target_urn IN (SELECT urn FROM ephemeral_ot)
    OR source_urn LIKE ANY (ARRAY(SELECT '%object-type:' || name FROM ephemeral_ot_names))
    OR target_urn LIKE ANY (ARRAY(SELECT '%object-type:' || name FROM ephemeral_ot_names));

-- Ontology versioning / properties / markings / branches
DELETE FROM ontology_review
 WHERE branch_id IN (
   SELECT id FROM ontology_branch WHERE object_type_urn IN (SELECT urn FROM ephemeral_ot)
 );
DELETE FROM ontology_branch WHERE object_type_urn IN (SELECT urn FROM ephemeral_ot);
DELETE FROM object_type_version WHERE object_type_urn IN (SELECT urn FROM ephemeral_ot);
DELETE FROM object_type_property WHERE object_type_urn IN (SELECT urn FROM ephemeral_ot);
DELETE FROM instance_marking WHERE object_type_urn IN (SELECT urn FROM ephemeral_ot);

DELETE FROM object_type WHERE urn IN (SELECT urn FROM ephemeral_ot);

-- Interfaces / VTs / SPTs
DELETE FROM interface_type WHERE {e_name};

DELETE FROM value_type_revision
 WHERE name IN (SELECT name FROM value_type WHERE {e_name});
DELETE FROM value_type WHERE {e_name};

DELETE FROM shared_property_type WHERE {e_api};

-- Strip ephemeral names from Object Type Groups
UPDATE object_type_group
SET object_types = (
  SELECT COALESCE(jsonb_agg(to_jsonb(x)), '[]'::jsonb)
  FROM jsonb_array_elements_text(object_types) AS t(x)
  WHERE NOT ({ephemeral("x")})
)
WHERE EXISTS (
  SELECT 1 FROM jsonb_array_elements_text(object_types) AS t(x)
  WHERE {ephemeral("x")}
);

COMMIT;
"""

    experience_sql = f"""
BEGIN;
DELETE FROM collection_member
 WHERE collection_id IN (
   SELECT id FROM collection WHERE {e_name}
 );
DELETE FROM collection WHERE {e_name};
DELETE FROM application WHERE {e_name};
COMMIT;
"""

    connectivity_sql = f"""
BEGIN;
DELETE FROM pipeline_run
 WHERE pipeline_name IN (SELECT name FROM pipeline_definition WHERE {e_name});
DELETE FROM pipeline_definition WHERE {e_name};
DELETE FROM generic_rest_source WHERE {e_name};
COMMIT;
"""

    print("Applying holon_knowledge…")
    psql("holon_knowledge", knowledge_sql)
    print("Applying holon_experience…")
    psql("holon_experience", experience_sql)
    print("Applying holon_connectivity…")
    psql("holon_connectivity", connectivity_sql)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete leftovers (default is dry-run inventory only)",
    )
    args = parser.parse_args()

    print("Scanning pytest leftovers…\n")
    before = inventory()
    width = max(len(label) for label, _ in before)
    total = 0
    for label, n in before:
        total += n
        print(f"  {label.ljust(width)}  {n}")
    print(f"\n  {'TOTAL'.ljust(width)}  {total}")
    print(f"\nDurable ObjectTypes preserved: {', '.join(DURABLE_OBJECT_TYPES)}")

    if total == 0:
        print("\nNothing to clean.")
        return

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to delete.")
        return

    print("\nDeleting…")
    apply_cleanup()
    print("\nAfter cleanup:\n")
    after = inventory()
    for label, n in after:
        print(f"  {label.ljust(width)}  {n}")
    print("\nDone. Restart knowledge if UI still caches lists; Vite FE will refetch.")


if __name__ == "__main__":
    main()
