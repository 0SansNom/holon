# Holon

An enterprise knowledge platform: a governed ontology (typed
`ObjectType`s and `RelationType`s, not raw tables) over heterogeneous
sources — Postgres, MongoDB, REST, file, and streaming connectors —
with ReBAC + ABAC authorization, column-level lineage, human-in-the-loop
governed writes (`Action`s with approval for high-risk ones), a
saga-based workflow engine, unified search, and an LLM agent runtime
scoped to the same governed paths a human user goes through.

## Where this actually stands

The ontology/governance/security core is real and tested against the
live stack, not mocked: masking is verified to actually strip
confidential fields for an unauthorized principal, not just labeled as
enforced; event-sourcing convergence is polled for and confirmed, not
assumed; the no-code connector and self-serve ObjectType creation are
exercised end to end.

It is **not** production-ready as-is. Scope is **OSS self-host** (see
[ADR 026](docs/adr/026-oss-self-host-scope.md)): we ship branchable
artefacts; we never operate inside a customer's SI. Multi-org
(**filiales** as `tenant`s on one instance) is in scope; SaaS pooled
multi-customer hosting is not.

Known gaps, in the order they'd actually bite for a self-hosting
enterprise:

- **Empty instance only** — Identity bootstraps one admin + tenant/workspace
  from env (`HOLON_TENANT_ID` / `HOLON_WORKSPACE_ID`, default workspace
  `main`); no bundled demo. Ontology, connectors, and extra principals
  are created through APIs (or CI fixtures — see
  [docs/ops/seed-data.md](docs/ops/seed-data.md)).
- **SSO / secrets / JWT ops** — OIDC + `SecretProvider` + JWT `kid`
  rotation and optional RS256 (`make gen-jwt-rsa`,
  `HOLON_JWT_REQUIRE_ASYMMETRIC`) are in-tree; Vault/IdP wiring and
  key custody are yours. Prefer connector `secret_ref`.
- **Intelligence is experimental** — leave
  `HOLON_INTELLIGENCE_ENABLED=false` in prod (posture-enforced). Joblib
  model upload and tool-plugin register are refused in production;
  tool-plugin `entry_point`s are prefix-allowlisted. Prefer
  `HOLON_LLM_PROVIDER=fake` locally; set
  `services.intelligence.runtimeClassName` (gVisor) in Helm.
- **Capacity & HA are yours.** CI is compose HTTP pytest;
  `make smoke-load` is a light probe only — no soak/chaos suite.
  Enable `HOLON_SERVING_STORE_REQUIRE_MATERIALIZED` in prod (posture +
  Helm overlay). Backup/DR tooling and HA data-plane are **your** SI.
- **Operator pack** — runbooks in `docs/ops/` (deploy, backup-restore,
  seed-data, observability), [`SECURITY.md`](SECURITY.md), Helm under
  `deploy/helm/holon/`, Prometheus artefacts under `deploy/observability/`,
  API overview in [`docs/api/overview.md`](docs/api/overview.md),
  API error contract in [`docs/api/errors.md`](docs/api/errors.md),
  `.github/workflows/publish.yml` for OCI+SBOM.

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
cp .env.example .env   # fill in real values — never commit .env
docker compose up -d --build   # or `make up`
```

After a **fresh** volume set, Identity creates tenant `acme`, workspace
`main`, and bootstrap admin `hl:acme:global:user:admin`. With
`HOLON_ALLOW_DEV_LOGIN=true` (compose default), sign in as that URN with
secret `admin-dev-secret` (UI at `http://localhost:8004`, or Vite below).
In production set `HOLON_BOOTSTRAP_ADMIN_SECRET` and
`HOLON_ALLOW_DEV_LOGIN=false` — see [docs/ops/seed-data.md](docs/ops/seed-data.md).

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
