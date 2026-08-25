# Holon
 
An enterprise knowledge platform that connects and organizes information from different systems into a unified business model. It provides a common way to understand and work with data, supports controlled actions and approvals when needed, enables workflows and search, and allows AI agents to work with the same information and business rules as human users.


## Where this actually stands

It is **not** production-ready as-is.

Known gaps, in the order they'd actually bite for a self-hosting
enterprise:

- **Empty instance only** — Identity bootstraps one admin + tenant/workspace
  from env (`HOLON_TENANT_ID` / `HOLON_WORKSPACE_ID`, default workspace
  `main`); no bundled demo. Ontology, connectors, and extra principals
  are created through APIs.
- **Intelligence is experimental** — leave
  `HOLON_INTELLIGENCE_ENABLED=false` in prod (posture-enforced). Joblib
  model upload and tool-plugin register are refused in production;
  tool-plugin `entry_point`s are prefix-allowlisted. Prefer
  `HOLON_LLM_PROVIDER=fake` locally; set
  `services.intelligence.runtimeClassName` (gVisor) in Helm.

Services expose `/metrics` (Prometheus text) and optional OTLP traces
(`HOLON_OTLP_ENDPOINT`; unset = tracing off). SLO recording rules, alerts,
and a Grafana dashboard live under `deploy/observability/` (optional Helm
`ServiceMonitor` / `PrometheusRule`) — see
[`docs/ops/observability.md`](docs/ops/observability.md). Point your own
observability stack at them.

## What's here

Six services, each its own FastAPI modulith with its own Postgres database:

| Service | Port | Role |
|---|---|---|
| `identity` | 8001 | Principals, ReBAC/ABAC policy decisions, token issuance |
| `connectivity` | 8002 | Source connectors (Postgres, MongoDB, REST, CSV/file, Kafka streaming) → Iceberg |
| `knowledge` | 8003 | Ontology, ontology-governed reads/writes, Actions, execution engine, search |
| `experience` | 8004 | The web UI (React SPA) and its Application Builder API |
| `automation` | 8005 | Workflow engine — sagas and compensation for multi-step Actions |
| `intelligence` | 8006 | LLM gateway, context builder, agent runtime, evaluation harness |

Plus infrastructure: Postgres, MinIO (S3), Iceberg REST catalog, Redpanda
(Kafka-compatible event bus), SpiceDB (ReBAC), OPA (ABAC), OpenSearch,
and Qdrant (semantic index).

Shared code (URN scheme, event envelope, transactional outbox, auth
primitives, plugin registry) lives in `libs/holon_common`.

## Running it

```bash
cp .env.example .env   # set a real HOLON_BOOTSTRAP_ADMIN_SECRET — never commit .env
docker compose up -d --build   # or `make up`
```

After a **fresh** volume set, Identity creates tenant `acme`, workspace
`main`, and bootstrap admin `hl:acme:global:user:admin` with the
`HOLON_BOOTSTRAP_ADMIN_SECRET` you set — sign in as that URN with that
secret (UI at `http://localhost:8004`, or Vite below). There is no
dev-login shortcut: local behaves the same as production here, and
`HOLON_BOOTSTRAP_ADMIN_SECRET` is required in every environment.

The platform starts **without** demo ObjectTypes or connectors. Create
them via the APIs, or for local/CI only:

```bash
make provision-test-fixtures   # principals / plugins / ObjectTypes via HTTP
make seed                      # raw rows into external source_erp
# then POST /sync (CI does this; see .github/workflows/tests.yml)
```

The frontend is served by `experience` at `http://localhost:8004`. For
frontend-only iteration against the real backend (faster reload):

```bash
cd services/experience/web
npm install
npm run dev   # http://localhost:5173, CORS to the services above
```

### Intelligence (optional)

- Default local: leave `HOLON_INTELLIGENCE_ENABLED` unset/true and set
  `HOLON_LLM_PROVIDER=fake` for no API spend (compose CI does this).
- Real models: put funded `ANTHROPIC_API_KEY` (and `VOYAGE_API_KEY` if
  `HOLON_EMBEDDING_PROVIDER=voyage`) in `.env`, set
  `HOLON_LLM_PROVIDER=anthropic`.
- Spend caps: `HOLON_INTELLIGENCE_RPM`,
  `HOLON_INTELLIGENCE_DAILY_TOKEN_QUOTA` (see `.env.example`).

## Tests

Layout: [`tests/README.md`](tests/README.md) — `tests/unit/` (no stack) and
`tests/integration/{service}/` (compose HTTP).

```bash
pip install -r tests/requirements.txt
make test-unit                            # fast; no compose
python3 -m pytest -q -m "not llm" tests   # full suite; needs stack
python3 -m pytest -q -m llm tests         # needs real keys + stack
```

PR CI runs unit first (no stack), then compose e2e with
`HOLON_LLM_PROVIDER=fake`, excluding metered LLM tests. Nightly metered
agent runs live in `.github/workflows/llm-nightly.yml`.

## License

MIT — see `LICENSE`.
