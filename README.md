# cachetic

[![PyPI version](https://img.shields.io/pypi/v/cachetic.svg)](https://pypi.org/project/cachetic/)
[![Python Version](https://img.shields.io/pypi/pyversions/cachetic.svg)](https://pypi.org/project/cachetic/)
[![License](https://img.shields.io/pypi/l/cachetic.svg)](https://opensource.org/licenses/MIT)

A simple, type-safe caching library supporting Redis and disk storage with automatic Pydantic serialization.

## Features

- **Type-safe**: Full type checking with generic support
- **Flexible backends**: Local disk cache (diskcache) or Redis
- **Pydantic integration**: Automatic serialization for any type via TypeAdapter
- **Compression support**: Optional zstd/zlib compression with automatic detection
- **Simple API**: Just `get()` and `set()` with optional TTL

## Installation

```bash
pip install cachetic
```

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
- **`cache_url`**: Cache backend - file path for disk cache or `redis://...` for Redis
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
