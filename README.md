# Holon

An enterprise knowledge platform: connectors ingest data from heterogeneous
sources into a governed ontology (typed `ObjectType`s and `RelationType`s,
not raw tables), with ReBAC + ABAC authorization, column-level lineage,
governed write paths (`Action`s with human-in-the-loop approval for
high-risk ones), a saga-based workflow engine, unified search, and an
LLM agent runtime that only ever acts through the same governed paths a
human user does.

## What's here

Seven services, each its own FastAPI modulith with its own Postgres database:

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
Qdrant (semantic index), Prometheus/Grafana/Jaeger.

Shared code (URN scheme, event envelope, transactional outbox, auth
primitives, plugin registry) lives in `libs/holon_common`.

## Running it

```bash
cp .env.example .env   # fill in real values — never commit .env
docker compose up -d --build
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
