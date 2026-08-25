.PHONY: infra-up infra-down build up down logs ps seed provision-test-fixtures test test-unit clean

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

# Seed test-only fixture data into external source systems
seed:
	$(COMPOSE) exec -T postgres psql -U holon -d source_erp < tests/fixtures/sql-seed/source_erp.sql
	$(COMPOSE) exec -T mongodb mongosh support_desk --quiet < tests/fixtures/mongo-init/init.js
	$(COMPOSE) --profile test-fixtures up -d reviews-api
	$(COMPOSE) --profile test-fixtures up -d oauth2-idp
	$(COMPOSE) --profile test-fixtures run --rm csv-seed
	$(COMPOSE) --profile test-fixtures run --rm source-s3-seed
	$(COMPOSE) --profile test-fixtures run --rm inventory-stream-seed

# Provision test fixtures via public APIs (CI)
provision-test-fixtures:
	python3 scripts/provision_test_fixtures.py

test-unit:
	pip3 install -q -r tests/requirements.txt
	python3 -m pytest -q -m unit tests

test:
	pip3 install -q -r tests/requirements.txt
	python3 -m pytest -q -m "not llm" tests
