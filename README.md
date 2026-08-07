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
exercised end to end. 332 integration tests, no unit-test theater.

It is **not** production-ready as-is. Known gaps, in the order they'd
actually bite:

- **Single tenant per deployment** — no multi-tenant isolation model.
- **Dev-only secrets everywhere** (`*-dev-secret`, a plaintext `.env`)
  and no SSO — just the seeded demo principals.
- **No load testing at real scale.** Everything here has only ever run
  on a 2-CPU/4GB local VM; two real missing-timeout bugs (S3, Qdrant)
  were found there, under trivial load, not synthetic production
  traffic.
- **CI runs tests, nothing else** — no build/push/deploy pipeline.
- **No backup/disaster-recovery story** for Postgres or the
  Iceberg/MinIO warehouse.

The bundled Prometheus/Grafana/Jaeger (see below) are for local dev —
a real deployment is expected to point its own observability stack at
the same `/metrics` and OTLP output every service already emits.

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
Qdrant (semantic index), and the dev-only Prometheus/Grafana/Jaeger
mentioned above.

Shared code (URN scheme, event envelope, transactional outbox, auth
primitives, plugin registry) lives in `libs/holon_common`.

## Running it

```bash
cp .env.example .env   # fill in real values — never commit .env
docker compose up -d --build            # core services only
docker compose --profile dev up -d --build   # + Prometheus/Grafana/Jaeger (also what `make up` does)
```

The frontend is served by `experience` at `http://localhost:8004`. For
frontend-only iteration against the real backend (faster reload):

```bash
cd services/experience/web
npm install
npm run dev   # http://localhost:5173, CORS to the services above
```

`intelligence` needs `ANTHROPIC_API_KEY` (and `VOYAGE_API_KEY` if
`HOLON_EMBEDDING_PROVIDER=voyage`) in `.env` — real, funded keys, added by
you directly.

## Tests

```bash
pip install -r tests/requirements.txt
python3 -m pytest -q tests -m "not llm"   # excludes tests that make real, metered LLM calls
python3 -m pytest -q tests -m llm         # only those, against a stack with real API keys configured
```

Black-box: every test talks to the running stack over HTTP, no service
internals imported (except `test_module_boundaries.py`, a static
`ast`-based check that needs no running stack at all).

## License

MIT — see `LICENSE`.
