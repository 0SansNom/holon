.PHONY: infra-up infra-down build up down logs ps seed provision-test-fixtures test test-unit test-soak lint clean

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
	$(COMPOSE) --profile test-fixtures up -d mysql
	$(COMPOSE) --profile test-fixtures run --rm csv-seed
	$(COMPOSE) --profile test-fixtures run --rm source-s3-seed
	$(COMPOSE) --profile test-fixtures up -d sftp
	$(COMPOSE) --profile test-fixtures run --rm inventory-stream-seed

# Provision test fixtures via public APIs (CI)
provision-test-fixtures:
	python3 scripts/provision_test_fixtures.py

test-unit:
	pip3 install -q -r tests/requirements.txt
	python3 -m pytest -q -m unit tests

# Pure Knowledge rules (validators, wire, glossary/query-log wrappers).
# These modules have no live stack; the gate is line coverage, not the HTTP suite.
test-knowledge-unit:
	pip3 install -q -r tests/requirements.txt
	python3 -m pytest -q -m unit tests/unit/knowledge \
		--cov=app.ontology.urns \
		--cov=app.ontology.type_classes \
		--cov=app.ontology.render_hints \
		--cov=app.ontology.lifecycle \
		--cov=app.ontology.relation_type_validate \
		--cov=app.ontology.value_type_validate \
		--cov=app.ontology.publishing_validate \
		--cov=app.ontology.publishing_validate_formats \
		--cov=app.ontology.publishing_validate_derived \
		--cov=app.ontology.publishing_validate_types \
		--cov=app.struct_values \
		--cov=app.api.object_wire \
		--cov=app.api.public_only \
		--cov=app.actions.wire \
		--cov=app.actions.notify \
		--cov=app.actions.hardcoded \
		--cov=app.actions.metrics \
		--cov=app.catalog_errors \
		--cov=app.catalog_state \
		--cov=app.search_metrics \
		--cov=app.search_constants \
		--cov=app.glossary \
		--cov=app.query_log \
		--cov-report=term-missing --cov-fail-under=100

# Identity rules with no live stack: claims, SAML/OIDC client, registry,
# governance, revocation, and the federated-login tail. Routers and SCIM
# stay on the HTTP suite.
test-identity-unit:
	pip3 install -q -r tests/requirements.txt
	python3 -m pytest -q -m unit tests/unit/identity \
		--cov=app.federation \
		--cov=app.saml \
		--cov=app.oidc \
		--cov=app.seed \
		--cov=app.governance \
		--cov=app.access_ops \
		--cov=app.federation_login \
		--cov=app.token_revocation \
		--cov=app.status_events \
		--cov=app.deps \
		--cov-report=term-missing --cov-fail-under=100

test:
	pip3 install -q -r tests/requirements.txt
	python3 -m pytest -q -m "not llm and not soak" tests

lint:
	pip3 install -q ruff==0.16.2
	python3 -m ruff check services libs cli tests
	cd services/experience/web && npm run check && npm test

test-soak:
	pip3 install -q -r tests/requirements.txt
	python3 -m pytest -q -m soak tests
