# Architecture

How Cachetic is put together, and the invariants that must not be broken.

## Layers

```
CacheticBase          configuration, key naming, TTL, serialisation — no I/O
├── Cachetic          4 sync operations  + cache          (CacheProtocol)
└── AsyncCachetic     4 async operations + await cache()  (AsyncCacheProtocol)
```

`cachetic/_base.py` holds everything that does not touch the network or disk.
Both clients inherit it, which is what keeps the storage format from forking:
there is exactly one implementation of `_dump_any` / `_loads_any`.

Backends live in `cachetic/extensions/` (sync) and `cachetic/extensions/aio/`
(async), each implementing a four-method protocol from `cachetic/types/`.

## Invariants

!!! danger "Do not break these"
    1. **One value format.** `Cachetic` and `AsyncCachetic` must produce
       byte-identical payloads. Pinned by
       `TestCrossClientInterop::test_identical_serialised_bytes`.
    2. **Old data stays readable.** `_loads_any` dispatches on three formats —
       Data URL (v0.7.0+), compressed legacy (v0.5.0+), raw bytes (v0.1.0+).
       Pinned by `tests/test_version_compat.py`.
    3. **Sync and async PostgreSQL create the same table.** Both use
       `CREATE TABLE IF NOT EXISTS`, so a mismatch diverges silently rather than
       raising. Pinned by `test_async_ddl_matches_sync_ddl`, which compares
       `information_schema`.
    4. **URL parsing is shared.** `cachetic/extensions/_url.py` is the only place
       that strips `?collection=` and `?table=`. psycopg rejects an unknown URI
       query parameter outright, so a second implementation would silently rot.

## Value format

Values are serialised as [Data URLs](https://developer.mozilla.org/en-US/docs/Web/URI/Schemes/data):

```data-url
data:application/json;compression=zstd;base64,<payload>
```

The compression algorithm travels with the value, so a reader never needs to know
the writer's settings. Because the payload is always base64, it is always ASCII —
which is why the PostgreSQL backend can store it in a `TEXT` column.

## Async client lifecycle

Async drivers bind to the event loop that created their connection pool. A client
reused from another loop raises `got Future attached to a different loop`.

`cachetic/extensions/aio/_registry.py` therefore keys shared clients by
`(loop, namespace, url)` and uses two locks for two different jobs:

| Guarded | Lock | Why |
|---------|------|-----|
| Registry lookup and client construction | `threading.Lock` | Every client factory is *synchronous*, so the lock is never held across an `await` and stays safe when several threads each run a loop |
| Awaitable setup — `create_index`, `CREATE TABLE`, `pool.open()` | `asyncio.Lock` stored **inside the entry** | Created under the loop that owns the entry, so it is never shared across loops |

!!! warning "Why not WeakKeyDictionary"
    Keying by a weak reference to the loop looks right and does nothing: every
    async client stores a reference back to its loop, so the value keeps the key
    alive and entries are never collected. Dead loops are swept explicitly on the
    next `acquire()` instead.

`close_all()` closes only the clients belonging to the running loop — a client
from another loop cannot be awaited from here. There is no reference counting, so
calling it while a request is in flight surfaces a raw driver error.

## Known trade-offs

These are decisions, not bugs. Change them only deliberately.

- **Disk async is thread offload.** `diskcache` has no async API. Cancelling an
  operation returns immediately while the worker thread finishes the write.
- **MongoDB and PostgreSQL expire late.** Both store a whole-second deadline from
  a truncated clock and compare with a strict `<`, so an entry can outlive its
  TTL by up to a second. It never expires early. Matching the two backends
  exactly was judged more valuable than sub-second accuracy.
- **`_ensured` state lives on the registry entry**, not in a module-level set, so
  a fresh loop with a fresh client re-creates its index or table.
