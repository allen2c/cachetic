# Changelog

Notable changes for people using this library. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

From v0.7.0 on, what may and may not change is fixed by
[six principles](https://allen2c.github.io/cachetic/PRINCIPLES/) — the first being that every version reads
every value an earlier version wrote and accepts every call v0.6.0 or later
accepted. The breaks listed under v0.1.0–v0.6.0 predate that promise.

## [0.7.0] - Unreleased

### Added

- **`AsyncCachetic`**, an async client mirroring `Cachetic` method for method on
  all four backends. Both write byte-identical values, so one cache can be read
  by a sync worker and an async web app at once, and a call site can be migrated
  one at a time.
- **`exists(key)`** on both clients — answers whether a key is present without
  fetching and deserialising the value.
- **`cachetic.close_all()`** and **`await cachetic.aio.close_all()`** to release
  shared backend clients. Safe to call more than once; a cache used afterwards
  reconnects rather than failing.
- **PostgreSQL backend**, via `pip install cachetic[postgres]`. Table name and
  pool size come from the URL: `?table=`, `?pool_min_size=`, `?pool_max_size=`.
- **`get` takes a real `default`**: `cache.get("missing", "fallback")` returns
  `"fallback"`. Earlier versions accepted the argument and discarded it.
- **`cache_url` accepts a client you built yourself** — a `redis.Redis` or
  `diskcache.Cache`, or a `redis.asyncio.Redis` for `AsyncCachetic`. It is used
  as-is, never entered into the shared registry, and **not** closed by
  `close_all()`: closing a pool this library did not open would break it for
  whatever else holds it. MongoDB and PostgreSQL stay URL-only, because their
  backends also need `?collection=` / `?table=`, which a bare client cannot carry.

### Changed

- **BREAKING — `redis` is no longer installed by default.** It moved from a core
  dependency to the `cachetic[redis]` extra, alongside `cachetic[mongodb]`,
  `cachetic[postgres]` and `cachetic[zstd]`. Only the disk backend ships in the
  base install. Add the extra if you use a `redis://` URL.
- **BREAKING — environment variables need the `CACHETIC_` prefix.** `CACHE_URL`
  becomes `CACHETIC_CACHE_URL`, and likewise for `DEFAULT_TTL`, `PREFIX` and
  `COMPRESSION`. Earlier versions read the bare names despite the documentation
  saying otherwise, which also let a generic `PREFIX` leak in from an unrelated
  part of the environment.
- **BREAKING — `default_ttl=0` now turns the whole client off.** Reads miss,
  `exists` reports `False`, and writes are dropped whatever `ex` an individual
  call carries. It used to drop writes only, so a client configured to disable
  caching kept serving whatever an earlier client had written. It still does not
  evict: values another client wrote stay where they are, and a per-call `ex=0`
  is unchanged — it skips that one write and leaves any existing entry alone.
- **BREAKING — `.cache` returns an adapter, not the driver.** It used to hand
  back the real `redis.Redis` / `diskcache.Cache`, so `cache.cache.scan_iter(...)`
  worked; it now returns a four-method `CacheProtocol`. This is deliberately not
  restored — the adapter is what lets every backend answer the same call the same
  way. To reach the driver's own methods, build the client yourself and pass it
  as `cache_url`.
- **BREAKING — `get_or_raise` no longer raises on a stored `None`.** For a cache
  whose type includes `None`, a key holding `None` is a hit. It used to be
  indistinguishable from a miss, so `get_or_raise` raised on keys `exists`
  reported as present.
- **BREAKING — zstd compression is now an extra.** `pip install cachetic[zstd]`
  rather than a side effect of having `zstandard` importable. Every process
  sharing a cache must agree: a writer that has it prefers zstd, and a reader
  without it cannot decompress the result.
- **BREAKING — the backend adapter protocol is positional-only.** `MongoCache`
  in particular no longer accepts `set(name=..., value=...)`. Affects only code
  calling an adapter directly rather than through `Cachetic`.
- **Values are stored as a self-describing Data URL** —
  `data:application/json;compression=zstd;base64,<payload>` — carrying the
  compression algorithm with the value, so a reader never needs to know the
  writer's settings. Values written by earlier versions are still read; no
  migration is needed.
- **Backend clients are shared per URL.** Constructing a `Cachetic` opens
  nothing, reaching for `.cache` opens nothing, and the first operation opens
  exactly one client that every other instance on that URL reuses — including
  the pools and monitor threads the driver keeps. Nothing is cached in your
  process, so a value another client deleted is gone here too.
- **The PostgreSQL backend no longer uses peewee.** Both clients talk to psycopg
  directly, so the extra no longer pulls in an ORM, the whole URL reaches psycopg
  (`sslmode` and friends now work on the sync client too), and the pool keeps one
  connection warm instead of four — a cache is optional infrastructure, and four
  becomes thirty-two across eight workers.
- **Extra arguments are accepted again.** `get`, `set`, `delete` and
  `get_or_raise` take stray positional and keyword arguments as they did in
  v0.6.0 and still ignore them, now with a `DeprecationWarning` naming what was
  dropped instead of discarding it in silence. `exists` is new in this release
  and takes exactly its documented arguments.
- `rich` and `str_or_none` are no longer dependencies, and the `pydantic` pin
  relaxed from `>=2,<3` to `>=2`.

### Fixed

- **`import cachetic` no longer requires `rich`.** v0.6.0 dropped `rich` from its
  install requirements while still importing it at module scope, so
  `pip install cachetic==0.6.0` followed by `import cachetic` raised
  `ModuleNotFoundError` in an environment that had nothing else pulling it in.
- **Values written by v0.2.0 with `object_type=str` are readable again.** They
  were stored as bare UTF-8 before the format became JSON in v0.3.0. Note the
  cost, which cannot be avoided: under that format every byte string is a valid
  value, so a `Cachetic[str]` can no longer tell a truncated write from a real
  v0.2.0 string, and logs a warning rather than raising. Other value types are
  unaffected. Values pickled by v0.2.0 under `object_type=object` remain
  deliberately unreadable — deserialising them executes arbitrary code.
- **Lazy expiry no longer discards a concurrent write.** MongoDB and PostgreSQL
  delete an entry when a read finds it expired; that delete now matches on the
  deadline it just read, so a `set` landing in between is not clobbered. Redis
  and diskcache enforce their own deadlines and were never affected.
- **zstd compression is safe to use from several threads.** `zstandard`'s
  one-shot API reuses an internal context, and one shared instance could crash
  the interpreter — Cachetic clients are designed to be shared. Contexts are now
  per thread.
- **`await cachetic.aio.close_all()` releases disk handles too.** They live in
  the synchronous registry, because `diskcache` has no async API and no event
  loop of its own; without this an application that only ever awaited the async
  teardown never closed a single SQLite handle.
- **A lost `CREATE TABLE IF NOT EXISTS` race is no longer fatal.** A fleet
  starting at once against an empty PostgreSQL database used to have one instance
  fail to start.

### Upgrading

`bytes` caches are the one case needing attention: a value written by v0.5.x or
v0.6.x with `compression=True` carries no algorithm marker, and every byte string
is a valid `bytes`, so there is nothing to detect. Keep `compression=True` on that
client to read its own old data. Values written from v0.7.0 on say which algorithm
they used and read back under either setting.

Everything else written by v0.1.0 onwards is read without migration, with the
v0.2.0 pickle exception noted above. See
[Upgrading to v0.7.0](https://github.com/allen2c/cachetic#upgrading-to-v070) for the details.

## [0.6.0] - 2026-03-18

### Added

- MongoDB clients are pooled: instances pointing at the same URL share one
  `MongoClient`, and the unique index is created once per database and collection
  rather than on every instantiation.

### Fixed

- Debug log lines for `get` / `set` are built only when debug logging is on.

### Known issue

- `rich` was removed from the install requirements but is still imported at
  module scope, so `import cachetic` fails in an environment that does not
  otherwise have it. Fixed in v0.7.0; installing `rich` alongside works around it.

## [0.5.0] - 2025-12-29

### Added

- **`compression`** option. Values are compressed before storage — zstd when
  `zstandard` is importable, zlib otherwise — and decompressed on read, with the
  format detected from the data.
- A published documentation site.

### Changed

- The `pydantic-settings` version pin was relaxed.

## [0.4.1] - 2025-08-20

### Changed

- **BREAKING — the MongoDB extra was renamed** from `cachetic[mongo]` to
  `cachetic[mongodb]`. Installs naming the old one fail.
- `rich` became a runtime dependency, used to pretty-print cache keys in debug
  logs.

## [0.4.0] - 2025-07-24

### Added

- **MongoDB backend.** Pass a `mongodb://` URL with a database path and a
  `?collection=` parameter, and install `cachetic[mongo]`.
- **`delete(key)`**.

## [0.3.0] - 2025-07-15

### Changed

- **BREAKING — `object_type` is now a `pydantic.TypeAdapter`.** `object_type=Person`
  becomes `object_type=pydantic.TypeAdapter(Person)`.
- **BREAKING — everything is stored as JSON** via the adapter, except `bytes`,
  which is still stored raw. This replaces the per-type encodings v0.2.0 used.
- **BREAKING — values written by v0.2.0 for `object_type=object` (pickle) and
  `object_type=str` (bare UTF-8) can no longer be read.** Those caches have to be
  repopulated. v0.7.0 restores the `str` half.
- **BREAKING — `cache_prefix` was renamed to `prefix`, `cache_ttl` to
  `default_ttl`**, and `cache_url` became required.
- `cache_url` also accepts an already-built `redis.Redis` or `diskcache.Cache`.

### Removed

- **BREAKING — caching arbitrary objects via `pickle`** (`object_type=object`).

## [0.2.0] - 2025-04-21

### Added

- `object_type` accepts `bytes`, `str`, `int`, `float`, `bool`, `list`, `dict`, a
  `pydantic.BaseModel` subclass, a `TypeAdapter`, or `object` for anything
  picklable. Each type has its own wire format — `str`, for instance, is stored as
  bare UTF-8 rather than JSON.
- The connection is checked on first use — a Redis `PING`, or a trial write for
  the disk cache — raising instead of failing later.
- `get_cache_key()`, returning the effective prefixed key.

### Changed

- **BREAKING — `cache_url` and `cache_dir` merged into `cache_url`.** Pass a
  `redis://` URL or a filesystem path; `cache_dir` is gone.
- **BREAKING — the default `object_type` became `object`**, so an
  unparameterised `Cachetic()` pickles values rather than validating them as a
  model.
- The `diskcache` and `redis` pins dropped their upper bound.

### Removed

- **BREAKING — `get_objects()`, `get_objects_or_raise()` and `set_objects()`**,
  with no replacement.

## [0.1.0] - 2025-02-23

Initial release. `Cachetic[T]` caches a Pydantic model type to a local
`diskcache` directory or a Redis server, with `get` / `get_or_raise` / `set` for
single objects, `get_objects` / `get_objects_or_raise` / `set_objects` for lists,
and TTL and key-prefix support. Values are serialised with `model_dump_json()`.
