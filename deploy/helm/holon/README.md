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
  nothing to configure. **That copy must be kept in sync by hand**:
  `files/spicedb-schema.zed` is a copy of `docker/spicedb/schema.zed`
  (Helm can't reference a file outside its own chart directory), so
  re-copy it whenever the source changes, before packaging a new chart
  version.
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

- **The bundled walking-skeleton demo connectors** (`demo.*` in
  `values.yaml`: a source ERP Postgres, a support-desk Mongo, a reviews
  REST API) are required at boot by `connectivity` today — real
  `os.environ[...]` reads, no fallback — even though a real deployer has
  no use for them. Left pointed at unreachable placeholders by default;
  the pod boots fine, only those specific demo connectors' own syncs
  would fail. Making this genuinely optional is an app-side fix, not a
  chart one.
- **No Ingress template** — front `holon-experience` (the web UI) and
  `holon-identity` (needs to be externally reachable for the OIDC
  redirect URI) with your own Ingress/Gateway.
