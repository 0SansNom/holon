.PHONY: infra-up infra-down build up down logs ps seed demo test clean

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

seed:
	$(COMPOSE) exec -T postgres psql -U holon -d source_erp < seed/source_erp.sql

demo: up
	python3 scripts/demo.py

test:
	pip3 install -q -r tests/requirements.txt
	python3 -m pytest -q tests
