# AGENTS.md

Type-safe caching library. `Cachetic` and `AsyncCachetic` over four backends:
disk, Redis, MongoDB, PostgreSQL.

## Where to look

| Question | Go to |
|----------|-------|
| What does the public API do? | [`README.md`](README.md) |
| How is this put together? What must not break? | [`docs/architecture.md`](docs/architecture.md) |
| How do I run tests, services, lint, docs? What are the code conventions? | [`docs/contributing.md`](docs/contributing.md) |
| What is still open? | `HANDOFF.md` — untracked, may not exist in a fresh clone |

Read `docs/architecture.md` before touching `_base.py`, `extensions/_url.py`,
either registry, or either PostgreSQL backend. Its **Invariants** section lists
what fails silently and which test pins it.

## Three things that bite

- **A green `make test` does not mean the backends ran.** They skip themselves
  when the service is down. Run `make services-up` first, or `make test-all`.
- **The sync and async halves mirror each other on purpose.** A change to one
  belongs in the other.
- **`README.md` and `docs/index.md` are the same content twice**, and every
  ```` ```python ```` block in `README.md` is executed as a test. See
  *Documentation* in `docs/contributing.md` before editing either.

## Before committing

```bash
make fmt
pyright --pythonpath "$(poetry env info -p)/bin/python"   # must stay at zero
```

Prove a fix by reverting the old behaviour and watching the new test go red.
