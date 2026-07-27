# AGENTS.md

Type-safe caching library. Sync (`Cachetic`) and async (`AsyncCachetic`) clients
over four backends: disk, Redis, MongoDB, PostgreSQL.

## Read first

| Question | Go to |
|----------|-------|
| How is this put together? What must not break? | [`docs/architecture.md`](docs/architecture.md) |
| How do I run tests, services, lint, docs? | [`docs/contributing.md`](docs/contributing.md) |
| What does the public API do? | [`README.md`](README.md) |

## Minimum you need to know

**Backend tests skip silently without services.** `make test` passing does not
mean the backends were exercised. Run `make services-up` first, or `make test-all`.

**`cachetic/_base.py` is shared by both clients.** It holds all serialisation and
has no I/O. Changing it changes the storage format for sync *and* async at once.

**Four invariants fail silently, not loudly** — one value format across both
clients, old data stays readable, sync/async PostgreSQL DDL match, URL parsing
shared. They are listed with their pinning tests in
[`docs/architecture.md`](docs/architecture.md#invariants). Read that section
before touching `_base.py`, `extensions/_url.py`, or either PostgreSQL backend.

**`README.md` and `docs/index.md` are the same content twice**, in Markdown and
mkdocs-material formats. Update both. `tests/test_readme_usages.py` executes the
README examples, so a broken example fails the build.

## Conventions

- `ruff` is the linter of record; its rules are pinned in `pyproject.toml`.
  `.flake8` exists only for editors. Run `make fmt` before committing.
- Backend adapters implement a four-method protocol and use positional-only
  parameters (`key`, `value`, `ex`), matching `cachetic/types/`.
- Add backend coverage through the `backend_url` fixture rather than writing a
  test per backend.
