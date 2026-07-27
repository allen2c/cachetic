# cachetic

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
pip install cachetic[postgres]        # + PostgreSQL (peewee + psycopg3 + pool)
pip install cachetic zstandard        # + zstd compression
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
        cache_url="redis://localhost:6379/0",
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

### Closing connections

Backend clients are shared per (event loop, URL), so closing is process-wide
rather than per-instance — one instance closing must not break another's client.
Call `close_all()` before the event loop exits:

```python
await close_all()
```

It closes only the clients belonging to the *running* loop. Clients whose loop
closed without a `close_all()` are dropped on the next use and left to the
garbage collector; an unclosed PostgreSQL pool in particular holds server
connections until then.

### Backend notes

| Backend | Driver | Notes |
|---------|--------|-------|
| Redis | `redis.asyncio` | Native async |
| MongoDB | `pymongo.AsyncMongoClient` | Native async, requires pymongo >= 4.9 |
| PostgreSQL | `psycopg` + `psycopg_pool` | Native async; the sync client uses peewee, both create the same table |
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

> All four backends share connections automatically — multiple `Cachetic` instances with
> the same URL reuse a single underlying client and skip redundant DDL / index creation.

## Compression

```python
cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url=".cache",
    compression=True,
)
```

- **zstd** (preferred) — install `zstandard`
- **zlib** (fallback) — Python standard library

Readers auto-detect compressed data regardless of their own `compression` setting, so
you can freely mix compressed and uncompressed writers.

## Configuration

| Parameter     | Type                   | Default | Description                              |
|---------------|------------------------|---------|------------------------------------------|
| `object_type` | `TypeAdapter[T]`       | —       | Pydantic type adapter for serialization  |
| `cache_url`   | `str \| pathlib.Path`  | —       | Backend URL or local path                |
| `default_ttl` | `int`                  | `-1`    | TTL in seconds (`-1` = no expiry)        |
| `prefix`      | `str`                  | `""`    | Key prefix for all operations            |
| `compression` | `bool`                 | `False` | Compress values before storage           |

### TTL

```python
cache = Cachetic[str](object_type=pydantic.TypeAdapter(str), default_ttl=3600)  # 1h
cache.set("key", "value", ex=300)  # per-call override: 5 min
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

| Method                        | Returns       | Description                            |
|-------------------------------|---------------|----------------------------------------|
| `get(key)`                    | `T \| None`   | Retrieve value, or `None` on miss      |
| `get_or_raise(key)`           | `T`           | Retrieve value, or raise on miss       |
| `set(key, value, ex=None)`    | `None`        | Store value with optional TTL          |
| `delete(key)`                 | `None`        | Remove a key                           |
| `exists(key)`                 | `bool`        | Check if a key exists                  |

`AsyncCachetic` exposes the same five methods as coroutines, plus the
module-level `cachetic.aio.close_all()`.

```python
from cachetic import CacheNotFoundError

result = cache.get("missing")          # None
cache.get_or_raise("missing")          # raises CacheNotFoundError
```

## Upgrading to v0.7.0

Two breaking changes:

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

```python
# before
mongo_cache.set(name="key", value=b"...")
# after
mongo_cache.set("key", b"...")
```

Stored data is unaffected — v0.7.0 reads everything written by earlier versions.

## License

MIT
