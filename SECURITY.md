# Security

Same model for people and agents: SpiceDB grants, then OPA restricts.
Confidential properties are stripped, not labelled. An agent never
exceeds its mandant. Tenants (filiales) are isolated on the read path
(search, Iceberg). Decisions are audited.

## Report a vulnerability

Private advisory on the GitHub repo (or email maintainers). Do not file
a public issue with exploit details.

## What the code enforces

`HOLON_ENV=production` refuses to boot if posture is wrong:

- `HOLON_METRICS_TOKEN` set; `HOLON_CORS_ORIGINS` set, no localhost
- `HOLON_MINTABLE_PRINCIPAL_URNS` set on every service (empty string if
  that service never mints)
- User JWT minting Identity-only (`HOLON_ALLOW_USER_JWT_MINT`)
- Intelligence off; joblib and in-process tool-plugin register off
- Knowledge serving store fail-closed (instance reads never fall back to Iceberg)

The production Helm overlay requires RS256 (`HOLON_JWT_ALG=RS256`,
`HOLON_JWT_REQUIRE_ASYMMETRIC=true`). HS256 (compose / `values.yaml`)
is a shared secret: a compromised pod can mint. Session cookie is
HttpOnly / Secure / SameSite=strict. Every JWT carries a `jti`;
`POST /logout` and disabling a principal revoke outstanding tokens
(Identity persists them; other services hydrate a snapshot on boot and
consume `identity.token.revoked` / `identity.principal.status_changed`).
Bootstrap always needs `HOLON_BOOTSTRAP_ADMIN_SECRET` — no dev-login.

Ingress: Experience + Identity only. Knowledge, Connectivity,
Automation, Intelligence stay internal. The Experience BFF rewrites the
`holon_session` cookie as `Authorization: Bearer` on Application and
lineage calls to Knowledge. `/api/knowledge`, `/api/connectivity`, and
`/api/intelligence` require a session; `/api/identity` stays public so
login can mint one.

## What you must set

TLS at ingress. NetworkPolicy. OpenSearch with its security plugin.
Pinned image tags. SpiceDB schema from
`deploy/helm/holon/files/spicedb-schema.zed`. Backups you drill —
[`docs/ops/backup-restore.md`](docs/ops/backup-restore.md). Deploy:
[`docs/ops/deploy.md`](docs/ops/deploy.md).

## Compose is not production

OpenSearch security is off. `HOLON_ENV` is empty, so posture checks are
a no-op. Ports are published on the host. Do not copy those defaults.

## Residual

Intelligence is experimental; in-process plugins are not a sandbox —
gVisor on that Deployment if you opt in. Connector APIs refuse plaintext
passwords / auth headers in production (`secret_ref` only). No soak/chaos
suite. RPO/RTO are yours.
