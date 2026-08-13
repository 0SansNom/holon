# Holon Helm chart

Deploys the 6 app services as Deployments + Services. Deliberately does
**not** bundle Postgres, Kafka, S3, SpiceDB, OPA, OpenSearch, or Qdrant —
bring your own (managed or self-run); point `values.yaml`'s `external.*`
at them.

## Prerequisites (external, not created by this chart)

- Postgres reachable at `external.postgresHost`, with one database per
  service already created: `holon_identity`, `holon_connectivity`,
  `holon_knowledge`, `holon_automation`, `holon_intelligence`,
  `holon_experience` (see `docker/postgres-init/01-init.sql` for the exact
  list this repo's own dev stack uses).
- Kafka-compatible bus (Redpanda or real Kafka) at `external.kafkaBootstrap`.
- S3-compatible object store at `external.s3Endpoint`, plus an Iceberg
  REST catalog (`external.icebergCatalogUri`) pointed at a warehouse path
  in it (`external.icebergWarehouse`).
- SpiceDB (`external.spicedbUrl`) — schema is loaded by `identity`,
  `knowledge`, and `experience` at startup from `HOLON_SPICEDB_SCHEMA_PATH`.
  The chart mounts it itself (`templates/spicedb-schema-configmap.yaml`,
  built from `files/spicedb-schema.zed`) into those three pods only —
  nothing to configure. **Keep the copy in sync** with
  `docker/spicedb/schema.zed` via `make sync-spicedb-schema` (CI runs
  `make check-spicedb-schema`). Helm cannot reference files outside the
  chart directory, so the checked-in copy is intentional.
- OPA (`external.opaUrl`), OpenSearch (`external.opensearchUrl`), Qdrant
  (`external.qdrantUrl`).
- An OTLP collector (`external.otlpEndpoint`) if you want traces — the
  exporter soft-fails (logged, non-blocking) without one.

## Secrets

This chart never creates or accepts raw secret material in `values.yaml`
— `existingSecret` (default `holon-secrets`) must be a Secret you create
yourself (directly, via your own GitOps, or via External Secrets) with at
least:

| Key | Used by |
|---|---|
| `HOLON_JWT_SECRET` | every service |
| `HOLON_SPICEDB_PRESHARED_KEY` | every service |
| `POSTGRES_PASSWORD` | every service |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | connectivity, knowledge, intelligence (S3/MinIO) |
| `HOLON_OPENSEARCH_PASSWORD` | knowledge |
| `ANTHROPIC_API_KEY` | intelligence |
| `HOLON_OIDC_CLIENT_SECRET` | identity, only if `oidc.enabled` |
| `VOYAGE_API_KEY` | intelligence, only if `HOLON_EMBEDDING_PROVIDER=voyage` |

`secretBackend` (default `kubernetes`) is read by `libs/holon_common`'s
pluggable secret provider — `env` (this chart's actual mechanism, despite
the name: the Secret's keys land as env vars via `envFrom`) or `vault` if
you've wired the app side to fetch from Vault directly instead.

## Known chart limitations

- **Connector backends** (`connectorBackends.*` in `values.yaml`) are
  ConfigMap URLs only. Plugins are never auto-registered — use
  `POST /plugins`. Leave empty in production unless you register matching
  plugins.
- **Ingress / NetworkPolicy** are optional (`ingress.enabled`,
  `networkPolicy.enabled`). Use `values-production.yaml` as a starting
  overlay; set `networkPolicy.dataPlaneCidrs` to your SI ranges, and
  optionally `networkPolicy.intelligence.llmEgressCidrs` for Anthropic /
  Voyage instead of open public `:443`.
- **No load / soak suite** in CI — e2e is compose HTTP pytest only. Treat
  capacity validation as an operator gate before go-live.
