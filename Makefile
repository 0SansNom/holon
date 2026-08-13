.PHONY: infra-up infra-down build up down logs ps seed provision-test-fixtures test clean sync-spicedb-schema check-spicedb-schema

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

test:
	pip3 install -q -r tests/requirements.txt
	python3 -m pytest -q tests
