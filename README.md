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
- **Agents** — optional, experimental. They use the same tools and
  policy as a human session.

One instance, N orgs (filiales). MIT.

It is **not production-ready**. Empty instance on first boot; you
create ontology, connectors, and principals through the APIs.
Intelligence stays off in production (`HOLON_INTELLIGENCE_ENABLED=false`,
enforced). See [`SECURITY.md`](SECURITY.md) and
[`docs/ops/deploy.md`](docs/ops/deploy.md).

## Services

Six FastAPI services, each with its own Postgres:

| Service | Port | Role |
|---|---|---|
| `identity` | 8001 | Principals, tokens, ReBAC/ABAC |
| `connectivity` | 8002 | Connectors (Postgres, MongoDB, REST, SQL, Kafka) → Iceberg |
| `knowledge` | 8003 | Ontology, governed reads/writes, Actions, search |
| `experience` | 8004 | Web UI and Application Builder |
| `automation` | 8005 | Workflows — sagas and compensation |
| `intelligence` | 8006 | LLM gateway, agents (experimental) |

Infra: Postgres, MinIO, Iceberg REST, Redpanda, SpiceDB, OPA,
OpenSearch, Qdrant. Shared primitives in `libs/holon_common`.

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
python3 -m pytest -q -m "not llm" tests   # needs the stack
```

## License

MIT — [`LICENSE`](LICENSE).
