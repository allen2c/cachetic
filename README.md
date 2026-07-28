# cachetic

[![PyPI version](https://img.shields.io/pypi/v/cachetic.svg)](https://pypi.org/project/cachetic/)
[![Python Version](https://img.shields.io/pypi/pyversions/cachetic.svg)](https://pypi.org/project/cachetic/)
[![License](https://img.shields.io/pypi/l/cachetic.svg)](https://opensource.org/licenses/MIT)

Type-safe caching for Python — multiple backends, Pydantic serialization, zero boilerplate.

Every release obeys [six principles](docs/PRINCIPLES.md), the first of which is
that data and calls from earlier versions keep working.

## Features

- **4 backends** — disk ([diskcache](https://github.com/grantjenks/python-diskcache)), Redis, MongoDB, PostgreSQL
- **Sync and async** — `Cachetic` and `AsyncCachetic`, same API, same storage format (v0.7.0)
- **Type-safe** — generic `Cachetic[T]` with full `TypeAdapter` support
- **Self-describing format** — [Data URL](https://developer.mozilla.org/en-US/docs/Web/URI/Schemes/data) serialization carries its own compression metadata (v0.7.0)
- **Compression** — optional zstd / zlib with automatic detection
- **Connection pooling** — shared connections and deduplicated DDL across all backends
- **Complete API** — `get`, `set`, `delete`, `exists` with optional TTL

## Installation

```bash
pip install cachetic                  # disk backend included
pip install cachetic[redis]           # + Redis
pip install cachetic[mongodb]         # + MongoDB
pip install cachetic[postgres]        # + PostgreSQL
pip install cachetic[zstd]            # + zstd compression
```

## Quick Start

```python
import pydantic
from cachetic import Cachetic

class Person(pydantic.BaseModel):
    name: str
    age: int

cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url=".cache",
)

cache.set("user:1", Person(name="Alice", age=30))
result = cache.get("user:1")   # Person(name='Alice', age=30)
cache.exists("user:1")         # True
cache.delete("user:1")
```

## Async

`AsyncCachetic` mirrors `Cachetic` method for method — same constructor, same
options, same stored bytes:

```python
import asyncio
import pydantic
from cachetic import AsyncCachetic
from cachetic.aio import close_all

async def main():
    cache = AsyncCachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url=".cache",   # or "redis://localhost:6379/0", unchanged otherwise
    )

    await cache.set("user:1", Person(name="Alice", age=30))
    result = await cache.get("user:1")   # Person(name='Alice', age=30)
    await cache.exists("user:1")         # True
    await cache.delete("user:1")

    await close_all()   # before the event loop exits

asyncio.run(main())
```

Both clients read each other's data on every backend, so you can migrate one call
site at a time, or run a sync worker alongside an async web app against the same
cache.

### Connection lifecycle

What a `Cachetic` costs, in order:

| Moment | Connections |
|--------|-------------|
| `Cachetic(...)` | none — construct your caches at import time, including ones you never use |
| `cache.cache` | none — the adapter holds a handle, not a client |
| First `get` / `set` / `delete` / `exists` | one client for that URL |
| Another `Cachetic` on the same URL | still one — clients are shared per URL, whatever the cached type |
| `close_all()` | none |

Nothing is cached in your process: every read goes to the backend, so a value
another client deleted is gone here too. Whatever pools and monitor threads the
driver keeps are the driver's, and you pay for them once per URL rather than once
per `Cachetic`.

The PostgreSQL pool keeps **one** connection warm, not psycopg's default of four,
because a cache is optional infrastructure and four becomes thirty-two across
eight workers. Raise it with `?pool_min_size=`.

This is [Principle 6](docs/PRINCIPLES.md), and it is what makes one cache per
cached model the intended shape rather than an expensive habit.

### Closing connections

Backend clients are shared per URL — per (event loop, URL) for the async ones —
so closing is process-wide rather than per-instance: one instance closing must
not break another's client. Both halves expose the same call:

```python
import asyncio
import cachetic
from cachetic.aio import close_all as aclose_all

cachetic.close_all()       # sync clients

async def shutdown():
    await aclose_all()     # async clients, before the event loop exits

asyncio.run(shutdown())
```

Either is safe to call more than once, and a cache used again afterwards
reconnects rather than failing.

The async one closes only the clients belonging to the *running* loop. Clients
whose loop closed without a `close_all()` are dropped on the next use and left
to the garbage collector — every driver's close is a coroutine, and there is no
live loop left to run it on. An unclosed PostgreSQL pool in particular holds
server connections until then.

### Backend notes

| Backend | Driver | Notes |
|---------|--------|-------|
| Redis | `redis.asyncio` | Native async |
| MongoDB | `pymongo.AsyncMongoClient` | Native async, requires pymongo >= 4.9 |
| PostgreSQL | `psycopg` + `psycopg_pool` | Both clients pool psycopg3 connections and share one table definition |
| Disk | `diskcache` | **Thread offload, not true async** — diskcache has no async API, so calls run in a worker thread. Cancelling an operation returns immediately but the thread still finishes the write |

## Backends

### Disk (default)

Any local path — string or `pathlib.Path`:

```python
cache = Cachetic[Person](object_type=pydantic.TypeAdapter(Person), cache_url=".cache")
```

### Redis

```python
cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url="redis://localhost:6379/0",
)
```

### MongoDB

```python
cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url="mongodb://localhost:27017/mydb?collection=mycache",
)
```

### PostgreSQL

```python
cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url="postgresql://user:pass@localhost:5432/mydb",
)
```

The table name and the connection-pool size come from the URL. Everything else in
it — `sslmode`, `application_name`, … — is handed to psycopg untouched, and the
sync and async clients read all of it identically.

| Parameter         | Default          | Description                          |
|-------------------|------------------|--------------------------------------|
| `table`           | `cachetic_cache` | Table holding the cache entries      |
| `pool_min_size`   | `1`              | Connections kept open per process    |
| `pool_max_size`   | `8`              | Ceiling on concurrent connections    |

```python
cache_url = "postgresql://user:pass@host/mydb?table=cache&pool_min_size=2&pool_max_size=20"
```

One warm connection is the default because a cache is optional infrastructure and
its cost multiplies across every process in a deployment. Raise `pool_min_size` if
the cache is on a hot path.

> All four backends share connections automatically — multiple `Cachetic` instances with
> the same URL reuse a single underlying client and skip redundant DDL / index creation.
> Release them with `cachetic.close_all()`.

### Bringing your own client

`cache_url` also takes a client you built yourself, when you want your own
connection settings rather than a URL:

```python
import diskcache

cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url=diskcache.Cache(".cache-i-opened-myself"),
)
```

`redis.Redis` works the same way, and `AsyncCachetic` takes a `redis.asyncio.Redis`.

**Cachetic does not close what you opened.** A client passed in this way is used
as-is, never entered into the shared connection registry, and left open by
`close_all()` — closing a pool the library did not open would break it for
whatever else is holding it. Closing it is yours to do.

MongoDB and PostgreSQL are configured by URL only. Their backends also need the
collection or table name, which arrives as `?collection=` / `?table=` — a bare
`MongoClient` does not carry it, so there is nothing to route it to.

## Compression

```python
cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url=".cache",
    compression=True,
)
```

- **zstd** (preferred) — `pip install cachetic[zstd]`
- **zlib** (fallback) — Python standard library

Values record which algorithm they used, so readers auto-detect compressed data
regardless of their own `compression` setting and you can freely mix compressed and
uncompressed writers.

Install the `zstd` extra on every process that shares a cache, or on none of them: a
writer that has `zstandard` prefers it, and a reader without the library cannot
decompress what that writer produced.

## Data URL Format (v0.7.0)

Cachetic serializes values as [Data URLs](https://developer.mozilla.org/en-US/docs/Web/URI/Schemes/data):

```data-url
data:application/json;compression=zstd;base64,<payload>
```

The compression algorithm is embedded in the URL itself, so the **reader doesn't need
to know the writer's settings**. No migration is required: anything that is not one of
the exact headers Cachetic emits is read as pre-v0.7.0 data.

**Reading pre-v0.7.0 `bytes` values.** Values written before v0.7.0 carry no algorithm
marker, and every byte string is a valid `bytes`, so there is nothing to detect. A
`bytes` cache reading data written by v0.5.x or v0.6.x needs `compression` set the way
the writer had it. Other value types are unaffected, and values written from v0.7.0 on
are self-describing.

## Configuration

| Parameter     | Type                   | Default | Description                              |
|---------------|------------------------|---------|------------------------------------------|
| `object_type` | `TypeAdapter[T]`       | —       | Pydantic type adapter for serialization  |
| `cache_url`   | `str \| pathlib.Path`  | —       | Backend URL or local path                |
| `default_ttl` | `int`                  | `-1`    | TTL in seconds (`-1` = no expiry, `0` = off) |
| `prefix`      | `str`                  | `""`    | Key prefix for all operations            |
| `compression` | `bool`                 | `False` | Compress values before storage           |

### TTL

```python
cache = Cachetic[str](
    object_type=pydantic.TypeAdapter(str),
    cache_url=".cache",
    default_ttl=3600,              # 1h
)
cache.set("key", "value", ex=300)  # per-call override: 5 min
cache.set("key", "value", ex=0)    # skip this write; leaves any existing entry
```

`default_ttl=0` is different: it turns the whole client off. Reads miss and writes
are dropped, so caching can be disabled from configuration alone. It does not
evict — values another client wrote stay where they are.

```python
off = Cachetic[str](
    object_type=pydantic.TypeAdapter(str),
    cache_url=".cache",
    default_ttl=0,
)
off.set("key", "value")
assert off.get("key") is None
assert off.exists("key") is False
```

> **Expiry precision.** Redis and diskcache enforce deadlines themselves. The
> MongoDB and PostgreSQL backends store a whole-second deadline taken from a
> truncated clock, so an entry there can outlive its TTL by up to a second
> (`ex=1` lives one to two seconds). Entries never expire *early*. This is the
> same on the sync and async clients, by design.

### Environment Variables

All fields accept `CACHETIC_` prefixed env vars:

```bash
export CACHETIC_CACHE_URL="redis://localhost:6379/0"
export CACHETIC_DEFAULT_TTL=3600
export CACHETIC_PREFIX="myapp"
export CACHETIC_COMPRESSION=true
```

## API Reference

| Method                        | Returns       | Description                                  |
|-------------------------------|---------------|----------------------------------------------|
| `get(key, default=None)`      | `T \| None`   | Retrieve value, or `default` on miss         |
| `get_or_raise(key)`           | `T`           | Retrieve value, or raise on miss             |
| `set(key, value, ex=None)`    | `None`        | Store value with optional TTL                |
| `delete(key)`                 | `None`        | Remove a key                                 |
| `exists(key)`                 | `bool`        | Check if a key exists                        |

`AsyncCachetic` exposes the same five methods as coroutines. Each half also has a
module-level teardown: `cachetic.close_all()` and `cachetic.aio.close_all()`.

```python
from cachetic import CacheNotFoundError

assert cache.get("missing") is None
assert cache.get("missing", "fallback") == "fallback"

try:
    cache.get_or_raise("missing")
except CacheNotFoundError:
    pass
```

`default` is returned only for a genuine miss. A cache whose type includes `None`
can store `None`, and reading that back is a hit — it returns `None`, not
`default`, and `get_or_raise` does not raise on it.

## Upgrading to v0.7.0

**1. Environment variables now require the `CACHETIC_` prefix.** Earlier versions
had no prefix configured, so the bare names below were read from the environment
despite the documentation saying otherwise. Unprefixed names are now ignored.

```bash
# before                     # after
CACHE_URL=...           →    CACHETIC_CACHE_URL=...
DEFAULT_TTL=...         →    CACHETIC_DEFAULT_TTL=...
PREFIX=...              →    CACHETIC_PREFIX=...
COMPRESSION=...         →    CACHETIC_COMPRESSION=...
```

This also stops generic names like `PREFIX` from leaking in from an unrelated
part of your environment.

**2. `MongoCache` methods are positional-only.** They now match the other three
backends and the `CacheProtocol` signature. Only affects code calling the adapter
directly rather than through `Cachetic`.

```text
# before
mongo_cache.set(name="key", value=b"...")
# after
mongo_cache.set("key", b"...")
```

**3. zstd compression is now an extra.** `pip install cachetic[zstd]` instead of
`pip install zstandard`. Every process sharing a cache must agree on it — a
writer that has the library prefers zstd, and a reader without it cannot
decompress the result.

**4. `get` has a real `default` parameter.** `get`, `set`, `delete` and
`get_or_raise` used to take `*args, **kwargs` and silently ignore them, so
`cache.get("key", "fallback")` — the `dict.get` habit — threw the fallback away
and returned `None`. The second argument to `get` now means what it reads as.

```python
cache.get("missing", "fallback")   # "fallback" — used to be None
```

Anything *beyond* that is still accepted and still ignored, now with a
`DeprecationWarning` naming what was dropped. Old calls keep working; they just
stop being silent.

```text
cache.delete("key", conn)          # runs, warns, ignores conn
```

`exists` is new in v0.7.0 and takes exactly its documented arguments.

**5. `default_ttl=0` now disables reads as well as writes.** It was documented as
"disable cache" but only dropped writes, so a client configured to turn caching
off kept serving whatever an earlier client had written. Reads now miss, `exists`
reports `False`, and writes are dropped whatever `ex` the call carries. A per-call
`ex=0` is unchanged: it skips that one write and leaves any existing entry alone.

**6. `get_or_raise` no longer raises on a stored `None`.** For a cache whose type
includes `None`, a key holding `None` is a hit — it used to be indistinguishable
from a miss, so `get_or_raise` raised on keys that `exists` reported as present.

**7. The PostgreSQL backend no longer uses peewee.** Both clients talk to psycopg
directly, so `pip install cachetic[postgres]` no longer pulls in an ORM, the
whole connection URL reaches psycopg (`sslmode` and friends now work on the sync
client too), and the pool keeps **one** connection warm instead of four. Raise it
with `?pool_min_size=`.

**8. `.cache` returns an adapter, not the driver.** It used to hand back the real
`redis.Redis` / `diskcache.Cache`, so `cache.cache.scan_iter(...)` worked. It now
returns a four-method `CacheProtocol`, which is what lets every backend answer the
same call the same way. If you need the driver's own methods, keep your own
reference and pass it in as `cache_url` — see *Bringing your own client*.

```text
cache.cache.scan_iter()    # AttributeError since v0.7.0
```

### Stored data

v0.7.0 reads everything written by earlier versions, with one exception: a cache
whose `object_type` is `bytes` and whose data was written by v0.5.x/v0.6.x with
`compression=True` must keep `compression=True` to read it. Those values carry no
algorithm marker and any byte string is a valid `bytes`, so there is nothing to
detect. Values written from v0.7.0 on say which algorithm they used and read back
under either setting.

## License

MIT
