# Cachetic

[![PyPI version](https://img.shields.io/pypi/v/cachetic.svg)](https://pypi.org/project/cachetic/)
[![Python Version](https://img.shields.io/pypi/pyversions/cachetic.svg)](https://pypi.org/project/cachetic/)
[![License](https://img.shields.io/pypi/l/cachetic.svg)](https://opensource.org/licenses/MIT)

Type-safe caching for Python — multiple backends, Pydantic serialization, zero boilerplate.

## Features

- **4 backends** — disk ([diskcache](https://github.com/grantjenks/python-diskcache)), Redis, MongoDB, PostgreSQL
- **Type-safe** — generic `Cachetic[T]` with full `TypeAdapter` support
- **Self-describing format** — [Data URL](https://developer.mozilla.org/en-US/docs/Web/URI/Schemes/data) serialization carries its own compression metadata (v0.7.0)
- **Compression** — optional zstd / zlib with automatic detection
- **Connection pooling** — shared connections and deduplicated DDL across all backends
- **Complete API** — `get`, `set`, `delete`, `exists`, `clear` with optional TTL

## Installation

```bash
pip install cachetic                  # disk backend included
pip install cachetic[redis]           # + Redis
pip install cachetic[mongodb]         # + MongoDB
pip install cachetic[postgres]        # + PostgreSQL (peewee + psycopg3)
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
cache.clear()
```

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

!!! info "Connection Pooling"
    All four backends share connections automatically — multiple `Cachetic` instances
    with the same URL reuse a single underlying client and skip redundant DDL / index
    creation.

## Compression

```python
cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url=".cache",
    compression=True,
)
```

| Algorithm | Priority  | Requirement                |
|-----------|-----------|----------------------------|
| **zstd**  | Preferred | `pip install zstandard`    |
| **zlib**  | Fallback  | Python standard library    |

!!! tip "Automatic Detection"
    Readers auto-detect compressed data regardless of their own `compression` setting.
    You can freely mix compressed and uncompressed writers — no migration needed.

## Data URL Format (v0.7.0)

Cachetic serializes values as [Data URLs](https://developer.mozilla.org/en-US/docs/Web/URI/Schemes/data):

```data-url
data:application/json;compression=zstd;base64,<payload>
```

The compression algorithm is embedded in the URL itself, so the **reader doesn't need
to know the writer's settings**. Legacy data (pre-v0.7.0) is auto-detected by the
absence of the `data:` prefix — no migration required.

## Configuration

### Constructor Parameters

| Parameter     | Type                   | Default | Description                              |
|---------------|------------------------|---------|------------------------------------------|
| `object_type` | `TypeAdapter[T]`       | —       | Pydantic type adapter for serialization  |
| `cache_url`   | `str \| pathlib.Path`  | —       | Backend URL or local path                |
| `default_ttl` | `int`                  | `-1`    | TTL in seconds (`-1` = no expiry)        |
| `prefix`      | `str`                  | `""`    | Key prefix for all operations            |
| `compression` | `bool`                 | `False` | Compress values before storage           |

### TTL

=== "No Expiration"

    ```python
    cache = Cachetic[str](
        object_type=pydantic.TypeAdapter(str),
        default_ttl=-1,  # default: never expires
    )
    ```

=== "1 Hour"

    ```python
    cache = Cachetic[str](
        object_type=pydantic.TypeAdapter(str),
        default_ttl=3600,
    )
    ```

=== "Per-Call Override"

    ```python
    cache.set("key", "value", ex=300)  # 5 minutes
    ```

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
| `clear()`                     | `None`        | Remove all entries from the backend    |

```python
from cachetic import CacheNotFoundError

result = cache.get("missing")          # None
cache.get_or_raise("missing")          # raises CacheNotFoundError
```

## License

MIT License — See [LICENSE](https://github.com/allenchou/cachetic/blob/main/LICENSE) for details.
