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
       Detection must stay narrow: a `bytes` cache stores its payload verbatim,
       so pre-0.7.0 data can itself begin with `data:`. Pinned by
       `tests/test_version_compat.py`.
    3. **Sync and async PostgreSQL create the same table.** Both use
       `CREATE TABLE IF NOT EXISTS`, so a mismatch diverges silently rather than
       raising. The schema lives once, in
       `cachetic/extensions/_postgres_sql.py`. Pinned by
       `test_sync_and_async_share_one_ddl_definition` and by
       `test_async_ddl_matches_sync_ddl`, which compares `information_schema`.
    4. **URL parsing is shared.** `cachetic/extensions/_url.py` is the only place
       that strips Cachetic's own parameters — `?collection=`, `?table=`,
       `?pool_min_size=`, `?pool_max_size=`. Everything else in the URL reaches
       the driver untouched, which is what keeps options like `sslmode` in effect
       for both clients. Pinned by `tests/test_url.py` and by
       `test_libpq_options_reach_both_backends`.
    5. **A connection URL never reaches a log line unmasked.** Registry keys are
       the raw URL — they have to be — and for Redis, MongoDB and PostgreSQL that
       carries the password. Every `logger` call in both registries passes it
       through `hide_url_password` first, and that function never raises and
       never echoes back a URL it could not redact. Pinned by
       `test_registry_never_logs_a_password` and
       `tests/utils/test_hide_url_password.py`.
    6. **A missing key and a stored `None` are different things.** `get` returns
       its `default` only for a real backend miss; a cache whose `T` includes
       `None` can store `None`, and reading it back is a hit. The distinction
       travels on the `MISSING` sentinel in `_base.py`, which is what stops
       `get_or_raise` raising on a key `exists` reports. Pinned by
       `tests/test_client_semantics.py`.

## Value format

Values are serialised as [Data URLs](https://developer.mozilla.org/en-US/docs/Web/URI/Schemes/data):

```data-url
data:application/json;compression=zstd;base64,<payload>
```

The compression algorithm travels with the value, so a reader never needs to know
the writer's settings. Because the payload is always base64, it is always ASCII —
which is why the PostgreSQL backend can store it in a `TEXT` column.

## Connection lifecycle

Backend clients are shared between every instance that names the same URL. An
application typically builds several `Cachetic` objects — one per cached type —
and each opening its own connections would waste them for no gain.

There are two registries with a deliberately identical shape:

| | `cachetic/extensions/_registry.py` | `cachetic/extensions/aio/_registry.py` |
|---|---|---|
| Key | `(namespace, url)` | `(loop, namespace, url)` |
| Teardown | `cachetic.close_all()` | `await cachetic.aio.close_all()` |
| Entry lock | `threading.Lock` | `asyncio.Lock` |
| Eviction | only `close_all()` | `close_all()`, plus a sweep of closed loops |

Both expose `Entry`, `EntryHandle`, `acquire` and `close_all` with the same
semantics, so what a contributor learns about one half applies to the other.
`test_sync_and_async_registries_expose_the_same_api` pins that.

**Adapters must never hold on to an `Entry`.** They keep an `EntryHandle` and
re-resolve through the registry on every operation, which costs one lock and one
dict lookup. That is what lets a cache used again after `close_all()` reconnect
instead of failing for the rest of the process. Pinned by
`test_instance_recovers_after_close_all` and its async twin.

Setup that must happen exactly once per client — `create_index`,
`CREATE TABLE`, `pool.open()` — is guarded by the entry's own lock and recorded
in `entry.ensured`, so a fresh client redoes it and a shared one does not.

!!! warning "The registry has no upper bound"
    Entries are keyed by URL and only removed by `close_all()` or a dead loop, so
    a URL built per request or per tenant keeps opening connections that are
    never reused. Cache URLs must come from a fixed, small set. Both registries
    warn once past `BUSY_REGISTRY_SIZE` entries, which is a smoke alarm, not a
    limit. Eviction is deliberately not implemented: closing a client that other
    live adapters are mid-operation on would trade a leak for a crash.

### Event-loop affinity

Async drivers bind to the event loop that created their connection pool. A client
reused from another loop raises `got Future attached to a different loop`. The
async registry therefore keys by `(loop, namespace, url)` and uses two locks for
two different jobs:

| Guarded | Lock | Why |
|---------|------|-----|
| Registry lookup and client construction | `threading.Lock` | Every client factory is *synchronous*, so the lock is never held across an `await` and stays safe when several threads each run a loop |
| Awaitable setup — `create_index`, `CREATE TABLE`, `pool.open()` | `asyncio.Lock` stored **inside the entry** | Created under the loop that owns the entry, so it is never shared across loops |

!!! warning "Why not WeakKeyDictionary"
    Keying by a weak reference to the loop looks right and does nothing: every
    async client stores a reference back to its loop, so the value keeps the key
    alive and entries are never collected. Dead loops are swept explicitly on the
    next `acquire()` instead — which, because adapters resolve through
    `EntryHandle`, is every cache operation.

`close_all()` closes only the clients belonging to the running loop — a client
from another loop cannot be awaited from here. There is no reference counting, so
calling it while a request is in flight surfaces a raw driver error.

Disk is the one exception, and it is deliberate. `diskcache` has no async API and
its handles are not loop-bound, so the async disk adapter shares the *synchronous*
registry's entries and there is no per-loop entry for `close_all` to find. The
async teardown therefore also closes the `DISK_NAMESPACE` entries; without that,
an application that only ever awaits `cachetic.aio.close_all()` would never
release a single SQLite handle. Pinned by
`test_close_all_releases_disk_handles`.

**Sweeping a dead loop cannot close its client.** Every close these drivers
offer — `Redis.aclose`, `AsyncMongoClient.close`, `AsyncConnectionPool.close` —
is a coroutine, and there is no live loop to run it on. The sweep drops the
reference and the garbage collector releases the socket; it also cannot run at
all while the process makes no cache calls. The first time this happens is
logged at WARNING rather than DEBUG, because it means connections outlived the
last chance to close them deliberately.

## Known trade-offs

These are decisions, not bugs. Change them only deliberately.

- **Disk async is thread offload.** `diskcache` has no async API. Cancelling an
  operation returns immediately while the worker thread finishes the write.
- **MongoDB and PostgreSQL expire late.** Both store a whole-second deadline from
  a truncated clock and compare with a strict `<`, so an entry can outlive its
  TTL by up to a second. It never expires early. Matching the two backends
  exactly was judged more valuable than sub-second accuracy.
- **Expiry is cleaned up lazily, on read.** `get` and `exists` delete the entry
  they found expired. That delete is conditional on the deadline they read — an
  unconditional delete-by-key would drop a value written by a concurrent `set`
  in between. Pinned by
  `test_expired_cleanup_does_not_drop_a_concurrent_write` in the Mongo and
  Postgres suites.
- **Pre-0.7.0 `bytes` values need the writer's `compression` setting.** They
  carry no algorithm marker, and every byte string validates as `bytes`, so
  there is no failure to recover from. Every other value type recovers on its
  own, and 0.7.0 values are self-describing.
- **Which compression algorithm gets used depends on the environment.** zstd
  when `zstandard` is importable, zlib otherwise. Values say which, so a reader
  only needs the matching library — but a reader without the `zstd` extra cannot
  read what a writer with it produced. Processes sharing a cache must agree on
  the extra.
- **PostgreSQL keeps one connection warm by default.** psycopg's own default is
  four, which multiplies across every process in a deployment for what is
  optional infrastructure. `?pool_min_size=` / `?pool_max_size=` raise it.
  `max_size` has to be set explicitly — psycopg reads `None` as "same as
  `min_size`", which would serialise every query.
- **`ensured` state lives on the registry entry**, not in a module-level set, so
  a fresh client re-creates its index or table and a shared one does not.
- **zstd contexts are per thread, not per process.** `zstandard`'s one-shot
  `compress`/`decompress` reuse an internal C context, so one shared instance
  segfaults the interpreter under concurrent use — and Cachetic clients are
  designed to be shared. `_ZSTD_LOCAL` in `cachetic/utils/compression.py` keeps
  the "build it once" saving without the sharing, at the cost of one context per
  thread. Pinned by `test_zstd_is_safe_from_many_threads`, which runs in a
  subprocess because the failure it guards against is a SIGSEGV.
- **Losing the `CREATE TABLE` race is not an error.** The entry lock serialises
  table creation within a process, but the registry is process-local and
  `IF NOT EXISTS` is not atomic across sessions. Both PostgreSQL backends catch
  `DuplicateTable` / `UniqueViolation` around the DDL and treat the table as
  ensured — a fleet starting at once against an empty database would otherwise
  have one instance fail to start. Caught outside the connection block so the
  rollback runs first.
- **`default_ttl=0` disables the client, it does not evict.** Reads miss, writes
  are dropped, and `exists` reports False, so caching can be switched off from
  configuration alone. Values another client wrote stay where they are. A
  per-call `ex=0` is narrower still: it skips that one write.
