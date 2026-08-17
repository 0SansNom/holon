.PHONY: infra-up infra-down build up down logs ps seed provision-test-fixtures test test-unit clean sync-spicedb-schema check-spicedb-schema smoke-load gen-jwt-rsa check-observability sync-observability

COMPOSE := docker compose

infra-up:
	$(COMPOSE) up -d postgres minio minio-init iceberg-rest redpanda

infra-down:
	$(COMPOSE) stop postgres minio minio-init iceberg-rest redpanda

build:
	$(COMPOSE) build identity connectivity knowledge automation experience intelligence

up: infra-up build
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f identity connectivity knowledge automation experience intelligence

ps:
	$(COMPOSE) ps

# Raw rows into the external source_erp DB (integration tests / local connectors).
# Platform principals/plugins/ObjectTypes are never auto-seeded — see
# `make provision-test-fixtures` for CI only.
seed:
	$(COMPOSE) exec -T postgres psql -U holon -d source_erp < seed/source_erp.sql

# CI / pytest fixtures via public APIs. Not a product feature.
provision-test-fixtures:
	python3 scripts/provision_test_fixtures.py

sync-spicedb-schema:
	./scripts/sync_spicedb_schema.sh --sync

check-spicedb-schema:
	./scripts/sync_spicedb_schema.sh --check

test-unit:
	pip3 install -q -r tests/requirements.txt
	python3 -m pytest -q -m unit tests

test:
	pip3 install -q -r tests/requirements.txt
	python3 -m pytest -q -m "not llm" tests

# Light concurrent /live|/ready probes (stack must already be up). Not a soak suite.
smoke-load:
	python3 scripts/smoke_load.py

# Print RS256 env snippets for .env / K8s Secret.
gen-jwt-rsa:
	python3 scripts/gen_jwt_rsa_keys.py

# Soft-check Prometheus rules when promtool is on PATH; ensure Helm copy is synced.
check-observability:
	@python3 scripts/sync_observability_rules.py --check
	@if command -v promtool >/dev/null 2>&1; then \
		promtool check rules deploy/observability/recording-rules.yaml deploy/observability/alerts.yaml; \
	else \
		echo "promtool not installed — skip (install prometheus/promtool to validate)"; \
	fi

# Refresh chart-embedded rules from deploy/observability/*.yaml
sync-observability:
	python3 scripts/sync_observability_rules.py
