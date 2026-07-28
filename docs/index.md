# Cachetic

[![PyPI version](https://img.shields.io/pypi/v/cachetic.svg)](https://pypi.org/project/cachetic/)
[![Python Version](https://img.shields.io/pypi/pyversions/cachetic.svg)](https://pypi.org/project/cachetic/)
[![License](https://img.shields.io/pypi/l/cachetic.svg)](https://opensource.org/licenses/MIT)

Type-safe caching for Python — multiple backends, Pydantic serialization, zero boilerplate.

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
options, same stored bytes.

=== "Async"

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
        result = await cache.get("user:1")
        await cache.exists("user:1")
        await cache.delete("user:1")

        await close_all()

    asyncio.run(main())
    ```

=== "Sync"

    ```python
    import pydantic
    from cachetic import Cachetic

    cache = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url="redis://localhost:6379/0",
    )

    cache.set("user:1", Person(name="Alice", age=30))
    result = cache.get("user:1")
    cache.exists("user:1")
    cache.delete("user:1")
    ```

!!! tip "Interoperable"
    Both clients read each other's data on every backend. You can migrate one call
    site at a time, or run a sync worker alongside an async web app against the
    same cache.

### Closing connections

Backend clients are shared per URL — per (event loop, URL) for the async ones —
so closing is process-wide rather than per-instance: one instance closing must
not break another's client. Both halves expose the same call.

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

!!! warning "Only the running loop"
    The async `close_all()` closes only the clients belonging to the loop that
    calls it. Clients whose loop closed without a `close_all()` are dropped on
    next use and left to the garbage collector — every driver's close is a
    coroutine, and there is no live loop left to run it on. An unclosed
    PostgreSQL pool holds server connections until then.

### Backend notes

| Backend | Driver | Notes |
|---------|--------|-------|
| Redis | `redis.asyncio` | Native async |
| MongoDB | `pymongo.AsyncMongoClient` | Native async, requires pymongo >= 4.9 |
| PostgreSQL | `psycopg` + `psycopg_pool` | Both clients pool psycopg3 connections and share one table definition |
| Disk | `diskcache` | Thread offload, not true async — see below |

!!! note "Disk backend is thread-offloaded"
    `diskcache` has no async API, so each call runs in a worker thread. This keeps
    the event loop responsive but is not true async I/O, and cancelling an
    operation returns immediately while the thread still finishes the write.

## Backends

=== "Disk (default)"

    Any local path — string or `pathlib.Path`:

    ```python
    cache = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url=".cache",
    )
    ```

=== "Redis"

    ```python
    cache = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url="redis://localhost:6379/0",
    )
    ```

=== "MongoDB"

    ```python
    cache = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url="mongodb://localhost:27017/mydb?collection=mycache",
    )
    ```

=== "PostgreSQL"

    ```python
    cache = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url="postgresql://user:pass@localhost:5432/mydb",
    )
    ```

### PostgreSQL URL parameters

The table name and the connection-pool size come from the URL. Everything else in
it — `sslmode`, `application_name`, … — is handed to psycopg untouched, and the
sync and async clients read all of it identically.

| Parameter       | Default          | Description                       |
|-----------------|------------------|-----------------------------------|
| `table`         | `cachetic_cache` | Table holding the cache entries   |
| `pool_min_size` | `1`              | Connections kept open per process |
| `pool_max_size` | `8`              | Ceiling on concurrent connections |

```python
cache_url="postgresql://user:pass@host/mydb?table=cache&pool_min_size=2&pool_max_size=20"
```

!!! tip "Why one connection by default"
    A cache is optional infrastructure and its cost multiplies across every process
    in a deployment — psycopg's own default of 4 becomes 32 across 8 workers, against
    a server that usually allows 100. Raise `pool_min_size` if the cache is on a hot
    path.

!!! info "Connection Pooling"
    All four backends share connections automatically — multiple `Cachetic` instances
    with the same URL reuse a single underlying client and skip redundant DDL / index
    creation. Release them with `cachetic.close_all()`.

## Compression

```python
cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url=".cache",
    compression=True,
)
```

| Algorithm | Priority  | Requirement                  |
|-----------|-----------|------------------------------|
| **zstd**  | Preferred | `pip install cachetic[zstd]` |
| **zlib**  | Fallback  | Python standard library      |

!!! tip "Automatic Detection"
    Values record which algorithm they used, so readers auto-detect compressed data
    regardless of their own `compression` setting. You can freely mix compressed and
    uncompressed writers — no migration needed.

!!! warning "Install the `zstd` extra everywhere, or nowhere"
    A writer that has `zstandard` available prefers it, and a reader without the
    library cannot decompress what that writer produced. Processes sharing a cache
    must agree on the extra.

## Data URL Format (v0.7.0)

Cachetic serializes values as [Data URLs](https://developer.mozilla.org/en-US/docs/Web/URI/Schemes/data):

```data-url
data:application/json;compression=zstd;base64,<payload>
```

The compression algorithm is embedded in the URL itself, so the **reader doesn't need
to know the writer's settings**. No migration is required: anything that is not one of
the exact headers Cachetic emits is read as pre-v0.7.0 data.

!!! note "Reading pre-v0.7.0 `bytes` values"
    Values written before v0.7.0 carry no algorithm marker, and every byte string is
    a valid `bytes`, so there is nothing to detect. A `bytes` cache reading data
    written by v0.5.x or v0.6.x needs `compression` set the way the writer had it.
    Other value types are unaffected, and values written from v0.7.0 on are
    self-describing.

## Configuration

### Constructor Parameters

| Parameter     | Type                   | Default | Description                              |
|---------------|------------------------|---------|------------------------------------------|
| `object_type` | `TypeAdapter[T]`       | —       | Pydantic type adapter for serialization  |
| `cache_url`   | `str \| pathlib.Path`  | —       | Backend URL or local path                |
| `default_ttl` | `int`                  | `-1`    | TTL in seconds (`-1` = no expiry, `0` = off) |
| `prefix`      | `str`                  | `""`    | Key prefix for all operations            |
| `compression` | `bool`                 | `False` | Compress values before storage           |

### TTL

=== "No Expiration"

    ```python
    cache = Cachetic[str](
        object_type=pydantic.TypeAdapter(str),
        cache_url=".cache",
        default_ttl=-1,  # default: never expires
    )
    ```

=== "1 Hour"

    ```python
    cache = Cachetic[str](
        object_type=pydantic.TypeAdapter(str),
        cache_url=".cache",
        default_ttl=3600,
    )
    ```

=== "Per-Call Override"

    ```python
    cache.set("key", "value", ex=300)  # 5 minutes
    cache.set("key", "value", ex=0)    # skip this write; leaves any existing entry
    ```

=== "Disabled"

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

!!! note "`default_ttl=0` turns the client off"
    Reads miss and writes are dropped, so caching can be disabled from
    configuration alone. It does not evict — values another client wrote stay
    where they are. A per-call `ex=0` is narrower: it skips that one write.

!!! warning "Expiry precision"
    Redis and diskcache enforce deadlines themselves. The MongoDB and PostgreSQL
    backends store a whole-second deadline taken from a truncated clock, so an
    entry there can outlive its TTL by up to a second (`ex=1` lives one to two
    seconds). Entries never expire *early*. This is identical on the sync and
    async clients, by design.

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

!!! note "A stored `None` is a hit, not a miss"
    `default` comes back only for a genuine miss. A cache whose type includes
    `None` can store `None`, and reading that back returns `None` rather than
    `default` — and `get_or_raise` does not raise on it.

## Upgrading to v0.7.0

!!! danger "Breaking changes"
    Seven things changed. Stored data is almost entirely unaffected — see
    [Stored data](#stored-data) for the one exception.

**1. Environment variables now require the `CACHETIC_` prefix.** Earlier versions
had no prefix configured, so the bare names were read from the environment
despite the documentation saying otherwise. Unprefixed names are now ignored,
which also stops generic names like `PREFIX` leaking in from an unrelated part of
your environment.

| Before          | After                    |
|-----------------|--------------------------|
| `CACHE_URL`     | `CACHETIC_CACHE_URL`     |
| `DEFAULT_TTL`   | `CACHETIC_DEFAULT_TTL`   |
| `PREFIX`        | `CACHETIC_PREFIX`        |
| `COMPRESSION`   | `CACHETIC_COMPRESSION`   |

**2. `MongoCache` methods are positional-only**, matching the other three backends
and the `CacheProtocol` signature. Only affects code calling the adapter directly
rather than through `Cachetic`.

```text
mongo_cache.set(name="key", value=b"...")   # before
mongo_cache.set("key", b"...")              # after
```

**3. zstd compression is now an extra.** `pip install cachetic[zstd]` instead of
`pip install zstandard`. Every process sharing a cache must agree on it — a writer
that has the library prefers zstd, and a reader without it cannot decompress the
result.

**4. `get`, `set`, `delete` and `get_or_raise` no longer accept `*args, **kwargs`.**
They took them and silently ignored them, so `cache.get("key", "fallback")` — the
`dict.get` habit — threw the fallback away and returned `None`. `get` now has a
real `default` parameter; the other three take exactly their documented arguments,
and anything else is a `TypeError`.

```python
cache.get("missing", "fallback")   # "fallback" — used to be None
```

**5. `default_ttl=0` now disables reads as well as writes.** It was documented as
"disable cache" but only dropped writes, so a client configured to turn caching
off kept serving whatever an earlier client had written. Reads now miss and
`exists` reports `False`. A per-call `ex=0` is unchanged: it skips that one write
and leaves any existing entry alone.

**6. `get_or_raise` no longer raises on a stored `None`.** For a cache whose type
includes `None`, a key holding `None` is a hit — it used to be indistinguishable
from a miss, so `get_or_raise` raised on keys that `exists` reported as present.

**7. The PostgreSQL backend no longer uses peewee.** Both clients talk to psycopg
directly, so `pip install cachetic[postgres]` no longer pulls in an ORM, the whole
connection URL reaches psycopg (`sslmode` and friends now work on the sync client
too), and the pool keeps **one** connection warm instead of four. Raise it with
`?pool_min_size=`.

### Stored data

v0.7.0 reads everything written by earlier versions, with one exception.

!!! warning "`bytes` caches written with compression before v0.7.0"
    A cache whose `object_type` is `bytes` and whose data was written by v0.5.x or
    v0.6.x with `compression=True` must keep `compression=True` to read it. Those
    values carry no algorithm marker and any byte string is a valid `bytes`, so
    there is nothing to detect. Values written from v0.7.0 on say which algorithm
    they used and read back under either setting.

## License

MIT License — See [LICENSE](https://github.com/allen2c/cachetic/blob/main/LICENSE) for details.
