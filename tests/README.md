# Holon tests

```text
tests/
  conftest.py                 # shared HTTP fixtures + auto-markers
  unit/                       # no compose — make test-unit
    holon_common/
    identity/
    knowledge/
    automation/
    intelligence/
  integration/                # compose HTTP / local infra
    knowledge/
    identity/
    connectivity/
    automation/
    intelligence/
    experience/
    platform/                 # cross-service / infra (dlq, migrations, …)
```

## Markers

| Marker | Meaning | Command |
|---|---|---|
| `unit` | No live stack (`tests/unit/`) | `make test-unit` |
| `integration` | Compose / local infra | `make test` / `pytest -m "not llm"` |
| `llm` | Real LLM spend | `pytest -m llm` (not default CI) |

Path `tests/unit/` → auto `unit`; everything else under `tests/` → auto `integration`.

## Conventions

- Prefer package folders over filename prefixes (`test_second_connector` → `integration/connectivity/…`).
- Phase suffixes (`_p2`, `_p3`) and `*_gaps` are avoided in new names.
- Stack helpers live in root `conftest.py` (`ontology_url`, `jdoe_token`, …).
- Do not mutate a live `holon_common` / `httpx` in `sys.modules` for stubs — only plant a stub when the name is absent, or use empty `types.ModuleType` / `MagicMock` knowing root `conftest` clears file-less third-party stubs between collected files.
