# Development
fmt:
	@isort cachetic tests
	@black cachetic tests
	@ruff check --fix cachetic tests

install:
	poetry install --all-extras --all-groups

update:
	poetry update

# Docs
mkdocs:
	mkdocs serve

# Tests
# Backend tests skip themselves unless the services are up; `make services-up`
# starts Redis, MongoDB and PostgreSQL on the ports tests/conftest.py expects.
services-up:
	docker compose up -d --wait

services-down:
	docker compose down -v

test:
	python -m pytest

test-all: services-up test
