# Security

How Holon expects to be secured when **self-hosted**. This is not a
managed SaaS security guarantee — you operate the SI. See
[ADR 026](docs/adr/026-oss-self-host-scope.md) and
[docs/ops/deploy.md](docs/ops/deploy.md).

## Reporting

If you believe you found a vulnerability in this repository, open a
**private** security advisory on the GitHub repo (or email the
maintainers listed in the repo metadata). Do not file a public issue
with exploit details.

## Trust boundaries (short)

| Boundary | Expectation |
|---|---|
| Browser → Experience / Identity | TLS at your Ingress; session cookie `HttpOnly`; CORS via explicit `HOLON_CORS_ORIGINS` |
| Service → service | Cluster network + shared JWT (`HOLON_JWT_SECRET` / rotation map). SA/agent minting is gated by per-service `HOLON_MINTABLE_PRINCIPAL_URNS`; user minting is Identity-only (`allow_user` + `HOLON_ALLOW_USER_JWT_MINT` in production) |
| AuthZ | SpiceDB ReBAC + OPA ABAC; decisions audited on logger `holon.audit` |
| Secrets | `HOLON_SECRET_BACKEND` (`env` / `kubernetes` / `vault` / `aws`); prefer `secret_ref` on connectors |
| Data plane | Your Postgres, S3/Iceberg, Kafka, OpenSearch, SpiceDB datastore |

When `HOLON_ENV=production` (or `prod`), every service calls
`assert_production_posture` at lifespan start and refuses to boot if the
flags below are wrong. JWT minting also hard-fails without the mint
allowlist / Identity user-mint flag.

## Production checklist

- [ ] `HOLON_ENV=production`
- [ ] `HOLON_ALLOW_DEV_LOGIN=false`
- [ ] Experience `POST /api/token` stays off when `HOLON_ALLOW_DEV_LOGIN=false`
- [ ] `HOLON_INTELLIGENCE_ENABLED=false` until the agent package is opted in
- [ ] OIDC enabled; no product demo / fixture scripts in prod
- [ ] `HOLON_BOOTSTRAP_ADMIN_SECRET` set for empty-instance / orphan repair
- [ ] `HOLON_BOOTSTRAP_ADMIN_RESET_SECRET` unset except during intentional break-glass
- [ ] `HOLON_METRICS_TOKEN` set; scrape only from a trusted network / NetworkPolicy
- [ ] `HOLON_CORS_ORIGINS` = real SPA origin(s) only (no `localhost` / `127.0.0.1`)
- [ ] `HOLON_MINTABLE_PRINCIPAL_URNS` set on every service (comma-separated full URNs and/or local-name suffixes; empty string OK if that service never mints SAs)
- [ ] `HOLON_ALLOW_USER_JWT_MINT=true` **only** on Identity; unset/false everywhere else
- [ ] `HOLON_SERVING_STORE_REQUIRE_MATERIALIZED=true` on Knowledge once materialization is the read path
- [ ] Connectivity / Intelligence HTTP mutations gated by SpiceDB workspace `read`/`write`
- [ ] Automation mint allowlist includes `ingest-bot` (chain-trigger hops)
- [ ] NetworkPolicy enabled in prod; pin `dataPlaneCidrs` / `intelligence.llmEgressCidrs` when known
- [ ] Image tag pinned (never `latest`); SBOM from publish workflow reviewed
- [ ] SpiceDB schema = `docker/spicedb/schema.zed` (Helm copy checked via `make check-spicedb-schema`)
- [ ] OpenSearch **with** security plugin (compose disables it for local only)
- [ ] Backups owned and drilled — [docs/ops/backup-restore.md](docs/ops/backup-restore.md)
- [ ] Ingress fronts Experience + Identity only; Knowledge/Connectivity stay internal unless intentionally exposed

## What compose deliberately weakens

Local `docker-compose.yml` is a **dev** stack: OpenSearch
`discovery.type=single-node` and `plugins.security.disabled=true`,
`HOLON_ALLOW_DEV_LOGIN=true`, optional empty OTLP, and `HOLON_ENV` left
empty so production posture checks are a no-op. Do not treat compose
defaults as a production posture.

## Known residual risks

- Shared HS256 JWT secret across services (service-account minting is
  allowlisted per service; full asymmetric / per-service keys are not yet
  the default).
- Example connector plugins under `services/connectivity/app/plugins/`
  are library code only — register via `POST /plugins` when you need them.
  Filiales use `/sources` / plugins. See
  [docs/ops/seed-data.md](docs/ops/seed-data.md).
