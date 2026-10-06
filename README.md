# Holon

An operating system for enterprise information.

Connect source systems. Model the business as objects, links, and
actions. Humans and agents work through that model — same types, same
rights, same approvals. Not a warehouse with a UI on top.

What that means in practice:

- **Ontology** — ObjectTypes, links, properties, markings. The schema
  is the product, not a side file.
- **Governance** — ReBAC (SpiceDB) then ABAC (OPA). Confidential
  fields are masked, not just labeled. Agents cannot exceed their
  mandant.
- **Actions** — mutations go through the ontology, with approval when
  the Action says so, and sagas when a step must compensate.
- **Applications** — a web UI and an application builder on the same
  APIs people and agents call.
- **Search** — one index over the ontology, tenant-scoped.
- **Agents** — optional, beta (opt-in) ontology-grounded agent runtime
  (not a general AI platform). Same tools and policy as a human session;
  off by default in production (see `services/intelligence/BETA.md`).

One deployment per tenant (subsidiary) — see [Tenancy](#tenancy). MIT.

It is **not production-ready**. Empty instance on first boot; you
create ontology, connectors, and principals through the APIs.
Intelligence stays off in production unless you explicitly opt into
beta (`HOLON_INTELLIGENCE_ENABLED` + `HOLON_INTELLIGENCE_BETA_OPT_IN`
+ sandbox RuntimeClass + subprocess plugin isolation + finite spend caps — see
[`services/intelligence/BETA.md`](services/intelligence/BETA.md)).
See [`SECURITY.md`](SECURITY.md) and the Helm production overlay.

## Services

Six FastAPI services, each with its own Postgres:

| Service | Port | Role |
|---|---|---|
| `identity` | 8001 | Principals, tokens, ReBAC/ABAC |
| `connectivity` | 8002 | Connectors (SQL, object storage, REST, SFTP, Salesforce, Kafka) → Iceberg |
| `knowledge` | 8003 | Ontology, governed reads/writes, Actions, search |
| `experience` | 8004 | Web UI and Application Builder |
| `automation` | 8005 | Workflows — sagas and compensation |
| `intelligence` | 8006 | Ontology-grounded agents / RAG (beta, opt-in) |

Infra: Postgres, MinIO, Iceberg REST, Redpanda, SpiceDB, OPA,
OpenSearch, Qdrant. Shared primitives in `libs/holon_common`.

## Tenancy

URNs, rows, search documents, SpiceDB tuples and audit records all carry
a tenant, and requests are scoped by the caller's token. The runtime is
narrower: each process reads one `HOLON_TENANT_ID` / `HOLON_WORKSPACE_ID`
pair (Helm `bootstrap.tenantId` / `bootstrap.workspaceId`), and service
accounts, background jobs (syncs, workflows, indexing), Experience and
default workspaces run as that pair. Run one deployment per tenant.

## Run

```bash
cp .env.example .env   # set HOLON_BOOTSTRAP_ADMIN_SECRET — never commit .env
docker compose up -d --build   # or make up
```

Fresh volumes: Identity creates tenant `acme`, workspace `main`, admin
`hl:acme:global:user:admin` with that secret. Sign in at
`http://localhost:8004`. No dev-login shortcut.

No demo ontology is bundled. Local/CI only:

```bash
make provision-test-fixtures
make seed
```

Frontend-only against the stack: `cd services/experience/web && npm run dev`
(`http://localhost:5173`).

Intelligence: `HOLON_LLM_PROVIDER=fake` locally. Real models need
`ANTHROPIC_API_KEY` and `HOLON_LLM_PROVIDER=anthropic`. Leave it off
in production.

## Tests

[`tests/README.md`](tests/README.md)

```bash
pip install -r tests/requirements.txt
make test-unit
python3 -m pytest -q -m "not llm and not soak" tests   # needs the stack.
```

## License

MIT — [`LICENSE`](LICENSE).
