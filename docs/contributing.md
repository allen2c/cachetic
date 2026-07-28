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
untouched code. `.flake8` exists only for editors that run flake8. Lines are 120
columns, set identically in all four places.

`pyright` is expected to report zero errors, and its settings are pinned in
`pyproject.toml` for the same reason as ruff's. Run it against the project
environment — the optional backends are dev dependencies, so a bare `pyright` on
an interpreter without them reports every extra as a missing import:

```bash
pyright --pythonpath "$(poetry env info -p)/bin/python"
```

## Tests

| File | Covers |
|------|--------|
| `test_async_cache.py` | Every async operation, parametrized over all four backends via the `backend_url` fixture |
| `test_async_registry.py` | Event-loop affinity — sequential loops, threads with their own loops, `close_all`, dead-loop sweeping |
| `test_sync_registry.py` | Client sharing, `close_all`, recovery after it, and concurrent construction |
| `test_version_compat.py` | Reading data written by v0.1.0 onwards |
| `test_url.py` | Connection-URL parsing, shared by the sync and async adapters |
| `test_postgres_cache.py` | Schema and connection-option parity between the two PostgreSQL backends, pool sizing, lazy expiry |
| `test_readme_executes.py` | Runs every ```` ```python ```` block in `README.md` |
| `test_readme_usages.py` | Asserts the *outcomes* the README examples describe |
| `test_client_semantics.py` | `get`'s `default`, stored `None` vs a miss, and the disabled client |
| `utils/test_compression.py` | zstd/zlib round-trips, format detection, and thread safety |
| `utils/test_hide_url_password.py` | Credential redaction — every log line and error message depends on it |

The `backend_url` fixture is the way to cover all four backends with one set of
assertions. Prefer it over writing per-backend tests.

Keys must be unique per test — backends are shared between tests and between
runs. `test_async_cache.py` has a `unique_key()` helper.

!!! danger "Every ```python block in README.md is executed"
    `test_readme_executes.py` extracts them and runs them in order into one
    shared namespace, in a temporary working directory — so a snippet may build
    on names an earlier one introduced, but may not depend on anything the
    README never shows. A snippet that cannot run is a failing test.

    Blocks that only illustrate something — a before/after diff, a shell
    command — must use a different fence language. Do not add a ```python fence
    you do not intend to be runnable.

    Snippets naming Redis, MongoDB or PostgreSQL are fine as long as they only
    *construct* a client: connecting is lazy, so no service is needed. A snippet
    that performs an operation has to use the disk backend.

    `test_readme_usages.py` is separate and still hand-kept. Execution proves a
    snippet runs; that file proves it does what the surrounding prose claims.

## Documentation

`README.md` and `docs/index.md` carry the same content in two formats — plain
Markdown for GitHub, mkdocs-material tabs and admonitions for the site. **Update
both.** `.github/workflows/docs.yml` runs `mkdocs build --strict` on every pull
request and deploys from `main` only if that passes.

```bash
make mkdocs           # serve locally
mkdocs build --strict # exactly what the pull-request job runs
```

## Conventions

### Module layout

Every module under `cachetic/` is laid out in this order:

1. Module docstring
2. Imports
3. Constants (including `__all__`, the logger, and module-level state)
4. Public functions
5. Public classes
6. Private functions and classes

Putting functions above classes means a return annotation naming a class defined
below has to be quoted — `-> "Entry"`. That is deliberate, not an oversight:
Python evaluates annotations at definition time, so the quotes are what make the
order legal. `cachetic/extensions/_registry.py` and `_url.py` are the examples.

Module-level `__getattr__` is the one exception and stays at the bottom with the
private definitions. It is import machinery, not part of the public surface.

### Everything else

- Backend adapters implement the four-method protocol in `cachetic/types/` and
  use positional-only parameters (`key`, `value`, `ex`).
- Adapters resolve their shared client through an `EntryHandle` on every
  operation. Never store the `Entry` — that breaks recovery after `close_all()`.
- The sync and async registries mirror each other on purpose. A change to one
  belongs in the other.
- Connection URLs are parsed in one place, `cachetic/extensions/_url.py`.

## Before changing behaviour

Read [Principles](PRINCIPLES.md) — five rules, and a change that breaks one does
not ship. Then read the invariants in [Architecture](architecture.md): every one
of them is the kind that fails silently rather than loudly, which is why each
names the test that pins it.

## Proving a fix

Reading the new code and agreeing with yourself is not evidence. Put the old
behaviour back, run the new test, and watch it fail — then restore the fix. A
test that passes against the bug it was written for is worse than no test,
because it reads like coverage.

This is not hypothetical here: of three tests written for one past fix, two
turned out to pin a different mechanism than their author assumed. The failure
mode is the test asserting something true both before and after.

Where the pre-fix failure is a crash rather than a wrong answer — the zstd
thread-safety test is the example — run it in a subprocess and assert on the
exit code, so a `SIGSEGV` fails one test instead of taking down the suite.
