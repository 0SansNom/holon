# Intelligence beta (opt-in)

Policy for enabling Holon Intelligence under `HOLON_ENV=production`.
Default remains **off**. This is **beta**, not GA.

## Why not experimental anymore

Intelligence now has migrations-first schema, SpiceDB ReBAC for
`agent_session` / `tool_plugin` / `ml_model`, spend caps, tenant-filtered
Qdrant, Agent App wiring, and action-path criteria. That is enough for a
**reviewed production opt-in**, not an unconditional ban.

## Production opt-in checklist

Boot (`assert_production_posture` on the Intelligence service) accepts
`HOLON_INTELLIGENCE_ENABLED=true` only when **all** of:

| Requirement | Env / Helm |
|---|---|
| Explicit acknowledgement | `HOLON_INTELLIGENCE_BETA_OPT_IN=true` / `intelligenceBetaOptIn: true` |
| Sandbox RuntimeClass | `HOLON_INTELLIGENCE_SANDBOX_RUNTIME` non-empty (e.g. `gvisor`) — must match Deployment `runtimeClassName` |
| Plugin isolation | `HOLON_TOOL_PLUGIN_ISOLATION=subprocess` (default; `inprocess` refused in prod) |
| Finite RPM | `HOLON_INTELLIGENCE_RPM` integer **> 0** |
| Finite daily tokens | `HOLON_INTELLIGENCE_DAILY_TOKEN_QUOTA` integer **> 0** |
| No pickle models | `HOLON_ALLOW_JOBLIB_MODELS` unset/false |
| No dynamic plugin register | `HOLON_ALLOW_TOOL_PLUGIN_REGISTER` unset/false |

Helm production overlay keeps `intelligenceEnabled: false`. To enable:

```yaml
intelligenceEnabled: true
intelligenceBetaOptIn: true
intelligenceRpm: 30
intelligenceDailyTokenQuota: 200000
toolPluginIsolation: subprocess
llmProvider: anthropic   # keys in existingSecret
services:
  intelligence:
    runtimeClassName: gvisor
```

The ConfigMap mirrors `runtimeClassName` into
`HOLON_INTELLIGENCE_SANDBOX_RUNTIME` when Intelligence is enabled.

## Agent App multi-turn

Interactive Agent App sessions stay `running` across turns; the UI reuses
one `sessionUrn` until **New chat** (or expiry/abort). Chain-trigger
sessions still complete after one turn and emit `session_completed`.

## Plugin sandbox

Agent tool plugins run in a **subprocess** per invoke
(`HOLON_TOOL_PLUGIN_ISOLATION=subprocess`) with
`HOLON_TOOL_PLUGIN_TIMEOUT_SECONDS` (default 15). A hung or crashing
plugin cannot take down the agent loop. Container RuntimeClass (gVisor)
remains the kernel-level sandbox for the Intelligence Deployment.
Dynamic `POST /tool-plugins` stays off in production; bake entry points
into the image under the allowlist.

## Soak / chaos

`tests/soak/intelligence/` (`pytest -m soak`) — concurrent sessions,
multi-turn reuse, brief pause resilience (fake LLM). Excluded from PR
e2e (`not soak`). Nightly: `.github/workflows/soak-nightly.yml`
(`make test-soak` locally with the stack up). RPM trip logic stays in
unit tests (`test_intelligence_spend_limits`).

## Still not GA

- Subprocess isolation is not a full seccomp/gVisor-per-plugin jail.
- Soak is correctness under concurrency, not a capacity/SLA proof.

## CI (no paid LLM on PRs)

PR / push CI uses `HOLON_LLM_PROVIDER=fake` and local embeddings.
`pytest -m llm` is **not** a merge gate — zero Anthropic/Voyage spend on
PRs. Optional real-LLM signal: `.github/workflows/llm-nightly.yml`
(skips cleanly when `ANTHROPIC_API_KEY` is unset). Local / manual:
`pytest -m llm` with your own keys.

## Compose / local

Non-production: Intelligence defaults on; beta flags and sandbox
attestation are not required (`HOLON_ENV` empty → posture no-op).
Plugin isolation defaults to `subprocess` in compose.
