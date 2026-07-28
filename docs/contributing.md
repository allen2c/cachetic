# Contributing

## Setup

```bash
make install          # poetry install --all-extras --all-groups
```

## Backing services

The Redis, MongoDB and PostgreSQL tests skip themselves when the service is not
reachable, so a bare `make test` always passes — it just covers less.

```bash
make services-up      # docker compose up -d --wait
make test             # backend tests now run instead of skipping
make services-down    # docker compose down -v
```

`make test-all` does the first two in one step.

Point the suite somewhere else with `CACHETIC_TEST_REDIS_URL`,
`CACHETIC_TEST_MONGO_URL` or `CACHETIC_TEST_POSTGRES_URL` if the default ports
clash with something local.

## Checks

```bash
make fmt              # isort + black + ruff --fix
make test             # pytest
```

CI runs the same lint and tests on Python 3.11, 3.12 and 3.13 with all three
services as containers.

`ruff` is the linter of record and its rule set is pinned in `pyproject.toml` —
without pinning, the defaults drift between releases and CI starts failing on
untouched code. `.flake8` exists only for editors that run flake8.

## Tests

| File | Covers |
|------|--------|
| `test_async_cache.py` | Every async operation, parametrized over all four backends via the `backend_url` fixture |
| `test_async_registry.py` | Event-loop affinity — sequential loops, threads with their own loops, `close_all`, dead-loop sweeping |
| `test_sync_registry.py` | Client sharing, `close_all`, recovery after it, and concurrent construction |
| `test_version_compat.py` | Reading data written by v0.1.0 onwards |
| `test_url.py` | Connection-URL parsing, shared by the sync and async adapters |
| `test_postgres_cache.py` | Schema and connection-option parity between the two PostgreSQL backends, pool sizing, lazy expiry |
| `test_readme_usages.py` | A hand-kept copy of the `README.md` examples |

The `backend_url` fixture is the way to cover all four backends with one set of
assertions. Prefer it over writing per-backend tests.

Keys must be unique per test — backends are shared between tests and between
runs. `test_async_cache.py` has a `unique_key()` helper.

!!! warning "README examples are copied, not executed"
    `test_readme_usages.py` does not read `README.md`. It is a hand-kept copy of
    the examples, so the two drift unless you change them together. Editing a
    README snippet means editing the matching test.

## Documentation

`README.md` and `docs/index.md` carry the same content in two formats — plain
Markdown for GitHub, mkdocs-material tabs and admonitions for the site. **Update
both.** The site deploys from `main` via `.github/workflows/docs.yml`.

```bash
make mkdocs           # serve locally
mkdocs build --strict # what CI-adjacent checks would catch
```

## Conventions

- Backend adapters implement the four-method protocol in `cachetic/types/` and
  use positional-only parameters (`key`, `value`, `ex`).
- Adapters resolve their shared client through an `EntryHandle` on every
  operation. Never store the `Entry` — that breaks recovery after `close_all()`.
- The sync and async registries mirror each other on purpose. A change to one
  belongs in the other.
- Connection URLs are parsed in one place, `cachetic/extensions/_url.py`.

## Before changing behaviour

Read the invariants in [Architecture](architecture.md). All four are the kind
that fail silently rather than loudly.
