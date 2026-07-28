# AGENTS.md

Type-safe caching library. `Cachetic` and `AsyncCachetic` over four backends:
disk, Redis, MongoDB, PostgreSQL.

## Where to look

| Question | Go to |
|----------|-------|
| What does the public API do? | [`README.md`](README.md) |
| How is this put together? What must not break? | [`docs/architecture.md`](docs/architecture.md) |
| How do I run tests, services, lint, docs? | [`docs/contributing.md`](docs/contributing.md) |

Read `docs/architecture.md` before touching `_base.py`, `extensions/_url.py`,
either registry, or either PostgreSQL backend. Its **Invariants** section lists
what fails silently and which test pins it.

## Before you trust a green test run

`make test` passing does not mean the backends were exercised — they skip
silently when the services are down. Run `make services-up` first, or
`make test-all`.

## Two things that are easy to get wrong

- **`README.md` and `docs/index.md` are the same content twice.** Update both.
- **The sync and async halves mirror each other on purpose.** A change to one
  belongs in the other.

Run `make fmt` before committing.
