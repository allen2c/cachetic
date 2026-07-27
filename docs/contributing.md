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
| `test_version_compat.py` | Reading data written by v0.1.0 onwards |
| `test_readme_usages.py` | The examples in `README.md`, executed for real |

The `backend_url` fixture is the way to cover all four backends with one set of
assertions. Prefer it over writing per-backend tests.

Keys must be unique per test — backends are shared between tests and between
runs. `test_async_cache.py` has a `unique_key()` helper.

!!! note "README examples are executed"
    `test_readme_usages.py` runs the code in `README.md`, so an example that
    does not work fails the build. Update it alongside the code.

## Documentation

`README.md` and `docs/index.md` carry the same content in two formats — plain
Markdown for GitHub, mkdocs-material tabs and admonitions for the site. **Update
both.** The site deploys from `main` via `.github/workflows/docs.yml`.

```bash
make mkdocs           # serve locally
mkdocs build --strict # what CI-adjacent checks would catch
```

## Before changing behaviour

Read the invariants in [Architecture](architecture.md). Three of them are the
kind that fail silently rather than loudly.
