# AGENTS.md

Type-safe caching library. `Cachetic` and `AsyncCachetic` over four backends:
disk, Redis, MongoDB, PostgreSQL.

## Read this first

[`docs/PRINCIPLES.md`](docs/PRINCIPLES.md) is the constitution — six rules every
release obeys. A task that cannot be done without breaking one is the thing that
is wrong. Amend the rule there first, in its own change, or drop the task.

## Where to look

| Question | Go to |
|----------|-------|
| What may a change never do? | [`docs/PRINCIPLES.md`](docs/PRINCIPLES.md) |
| What does the public API do? | [`README.md`](README.md) |
| How is this put together? What must not break? | [`docs/architecture.md`](docs/architecture.md) |
| How do I run tests, services, lint, docs? What are the conventions? | [`docs/contributing.md`](docs/contributing.md) |
| What is still open? | `HANDOFF.md` — untracked, may not exist in a fresh clone |

## Three things that bite

- **A green `make test` does not mean the backends ran.** They skip themselves
  when the service is down. Run `make services-up` first, or `make test-all`.
- **The sync and async halves mirror each other on purpose.** A change to one
  belongs in the other.
- **Every ```` ```python ```` block in `README.md` and `docs/index.md` is executed
  as a test.** The two are the same content in two formats — update both.

## Before committing

```bash
make fmt
pyright --pythonpath "$(poetry env info -p)/bin/python"   # must stay at zero
```

Prove a fix by reverting the old behaviour and watching the new test go red.
