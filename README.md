# cachetic

[![PyPI version](https://img.shields.io/pypi/v/cachetic.svg)](https://pypi.org/project/cachetic/)
[![Python Version](https://img.shields.io/pypi/pyversions/cachetic.svg)](https://pypi.org/project/cachetic/)
[![License](https://img.shields.io/pypi/l/cachetic.svg)](https://opensource.org/licenses/MIT)

A simple, type-safe caching library supporting Redis and disk storage with automatic Pydantic serialization.

## Features

- **Type-safe**: Full type checking with generic support
- **Flexible backends**: Local disk cache (diskcache), Redis, or MongoDB
- **Pydantic integration**: Automatic serialization for any type via TypeAdapter
- **Compression support**: Optional zstd/zlib compression with automatic detection
- **Connection pooling**: Shared MongoClient instances and deduplicated index creation (v0.6.0)
- **Simple API**: Just `get()` and `set()` with optional TTL

## Installation

```bash
pip install cachetic
```

## Upgrading to v0.7.0 — read this first

v0.7.0 changes the stored value format, and **v0.6.0 cannot read what a v0.7.0
process writes**. On a shared cache that matters during any rolling deployment:
while both versions are live, every value a v0.7.0 pod writes is unreadable to
the v0.6.0 pods beside it — an exception for most types, and silently wrong
bytes for a `Cachetic[bytes]`. With the default `default_ttl=-1` those values do
not expire, so rolling back does not clear them.

**v0.6.1 exists only to make that upgrade safe.** It reads the v0.7.0 format and
still writes the old one, so it is compatible in both directions:

```bash
pip install "cachetic>=0.6.1,<0.7"   # step 1: deploy everywhere
pip install "cachetic>=0.7"          # step 2: only once step 1 is fully rolled out
```

Each step is safe to have half-deployed, and safe to roll back. Skipping step 1
is what breaks.

Two notes if you use `compression=True`:

- Install the same compression support on both sides. A v0.7.0 process with
  `cachetic[zstd]` writes zstd frames; a reader without the library raises
  `DecompressionError` rather than guessing. Either install `cachetic[zstd]`
  everywhere — it is available on 0.6.1 for exactly this reason — or nowhere.
- v0.6.1 always compresses with zlib, where v0.6.0 used zstd whenever
  `zstandard` happened to be importable. This is deliberate: it keeps 0.6.1's
  writes readable by the 0.6.0 pods it is rolling over, whatever either image
  has installed. v0.6.0 and v0.7.0 both read zlib.

## Quick Start

### Basic Usage

```python
import pydantic
from cachetic import Cachetic

# Define your model
class Person(pydantic.BaseModel):
    name: str
    age: int

# Create cache instance
cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url=".cache"  # Local disk cache
)

# Store and retrieve
person = Person(name="Alice", age=30)
cache.set("user:1", person)

result = cache.get("user:1")
print(result.name)  # "Alice"
```

### Redis Backend

```python
cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url="redis://localhost:6379/0"
)
```

### MongoDB Backend

```python
pip install cachetic[mongodb]
```

```python
cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url="mongodb://localhost:27017/mydb?collection=mycache"
)
```

Multiple `Cachetic` instances sharing the same MongoDB connection string automatically
reuse a single `MongoClient`, avoiding repeated authentication handshakes and redundant
index creation.

### Primitive Types

```python
# String cache
str_cache = Cachetic[str](
    object_type=pydantic.TypeAdapter(str),
    cache_url=".cache"
)

str_cache.set("greeting", "Hello, World!")
print(str_cache.get("greeting"))  # "Hello, World!"

# List cache
list_cache = Cachetic[list[str]](
    object_type=pydantic.TypeAdapter(list[str]),
    cache_url=".cache"
)

list_cache.set("items", ["apple", "banana", "cherry"])
```

### Complex Types

```python
from typing import Dict, List

# Dictionary cache
data = {"users": [{"id": 1, "name": "Alice"}], "total": 1}
dict_cache = Cachetic[Dict](
    object_type=pydantic.TypeAdapter(Dict),
    cache_url=".cache"
)

dict_cache.set("user_data", data)

# List of models
people_cache = Cachetic[List[Person]](
    object_type=pydantic.TypeAdapter(List[Person]),
    cache_url=".cache"
)

people = [Person(name="Alice", age=30), Person(name="Bob", age=25)]
people_cache.set("team", people)
```

### Compression Support

Enable compression to reduce storage space and bandwidth usage:

```python
# Enable compression (auto-selects best algorithm)
cache = Cachetic[Person](
    object_type=pydantic.TypeAdapter(Person),
    cache_url=".cache",
    compression=True  # New in v0.5.0
)

person = Person(name="Alice", age=30)
cache.set("user:1", person)  # Automatically compressed
result = cache.get("user:1")  # Automatically decompressed
```

**Compression Algorithms:**

- **zstd** (preferred): Used if `zstandard` package is installed
- **zlib** (fallback): Built-in Python standard library

**Automatic Detection:**

- Caches with `compression=False` can still read compressed data
- Automatic decompression occurs when compressed data is detected
- Seamless migration between compressed and uncompressed caches

**Installation with zstd support:**

```bash
pip install cachetic zstandard
```

## Configuration

### Constructor Parameters

- **`object_type`**: `pydantic.TypeAdapter[T]` - Required type adapter for serialization
- **`cache_url`**: Cache backend - file path for disk cache, `redis://...` for Redis, or `mongodb://...` for MongoDB
- **`default_ttl`**: Default expiration in seconds (`-1` = no expiration, `0` = disabled)
- **`prefix`**: Key prefix for all cache operations
- **`compression`**: Enable compression for cached values (default: `False`)

### TTL Examples

```python
# No expiration (default)
cache = Cachetic[str](
    object_type=pydantic.TypeAdapter(str),
    default_ttl=-1
)

# 1 hour expiration
cache = Cachetic[str](
    object_type=pydantic.TypeAdapter(str),
    default_ttl=3600
)

# Per-operation TTL
cache.set("key", "value", ex=300)  # 5 minutes
```

### Environment Variables

Use `CACHETIC_` prefix:

```bash
export CACHETIC_CACHE_URL="redis://localhost:6379/0"
export CACHETIC_DEFAULT_TTL=3600
export CACHETIC_PREFIX="myapp"
export CACHETIC_COMPRESSION=true
```

## Error Handling

```python
from cachetic import CacheNotFoundError

# get() returns None for missing keys
result = cache.get("nonexistent")  # None

# get_or_raise() throws exception
try:
    result = cache.get_or_raise("nonexistent")
except CacheNotFoundError:
    print("Key not found")
```

## License

MIT License
