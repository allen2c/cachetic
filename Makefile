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
test:
	python -m pytest
