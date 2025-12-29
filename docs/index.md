# Cachetic

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

For compression support with zstd:

```bash
pip install cachetic zstandard
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

## Usage Examples

### Primitive Types

=== "String Cache"

    ```python
    str_cache = Cachetic[str](
        object_type=pydantic.TypeAdapter(str),
        cache_url=".cache"
    )

    str_cache.set("greeting", "Hello, World!")
    print(str_cache.get("greeting"))  # "Hello, World!"
    ```

=== "List Cache"

    ```python
    list_cache = Cachetic[list[str]](
        object_type=pydantic.TypeAdapter(list[str]),
        cache_url=".cache"
    )

    list_cache.set("items", ["apple", "banana", "cherry"])
    ```

### Complex Types

=== "Dictionary"

    ```python
    from typing import Dict

    data = {"users": [{"id": 1, "name": "Alice"}], "total": 1}
    dict_cache = Cachetic[Dict](
        object_type=pydantic.TypeAdapter(Dict),
        cache_url=".cache"
    )

    dict_cache.set("user_data", data)
    ```

=== "List of Models"

    ```python
    from typing import List

    people_cache = Cachetic[List[Person]](
        object_type=pydantic.TypeAdapter(List[Person]),
        cache_url=".cache"
    )

    people = [Person(name="Alice", age=30), Person(name="Bob", age=25)]
    people_cache.set("team", people)
    ```

## Compression Support

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

### Compression Algorithms

| Algorithm | Priority  | Requirement                        |
|-----------|-----------|------------------------------------|
| **zstd**  | Preferred | Install `zstandard` package        |
| **zlib**  | Fallback  | Python standard library (built-in) |

!!! tip "Automatic Detection"
    - Caches with `compression=False` can still read compressed data
    - Automatic decompression occurs when compressed data is detected
    - Seamless migration between compressed and uncompressed caches

## Configuration

### Constructor Parameters

| Parameter     | Type             | Default  | Description                                                  |
|---------------|------------------|----------|--------------------------------------------------------------|
| `object_type` | `TypeAdapter[T]` | Required | Type adapter for serialization                               |
| `cache_url`   | `str`            | Required | File path for disk cache or `redis://...` for Redis          |
| `default_ttl` | `int`            | `-1`     | Expiration in seconds (`-1` = no expiration, `0` = disabled) |
| `prefix`      | `str`            | `""`     | Key prefix for all cache operations                          |
| `compression` | `bool`           | `False`  | Enable compression for cached values                         |

### TTL Examples

=== "No Expiration"

    ```python
    cache = Cachetic[str](
        object_type=pydantic.TypeAdapter(str),
        default_ttl=-1  # Default: never expires
    )
    ```

=== "1 Hour Expiration"

    ```python
    cache = Cachetic[str](
        object_type=pydantic.TypeAdapter(str),
        default_ttl=3600
    )
    ```

=== "Per-Operation TTL"

    ```python
    # Override default TTL for specific operations
    cache.set("key", "value", ex=300)  # 5 minutes
    ```

### Environment Variables

Configure cachetic using environment variables with `CACHETIC_` prefix:

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

## API Reference

### Core Methods

#### `get(key: str) -> T | None`

Retrieves a cached value by key.

**Returns**: The cached object or `None` if not found.

#### `get_or_raise(key: str) -> T`

Retrieves a cached value by key, raising an exception if not found.

**Raises**: `CacheNotFoundError` if the key doesn't exist.

#### `set(key: str, value: T, ex: int | None = None) -> None`

Stores a value in the cache.

**Parameters**:

- `key`: Cache key
- `value`: Object to cache
- `ex`: Optional TTL in seconds (overrides `default_ttl`)

#### `delete(key: str) -> None`

Removes a key from the cache.

## License

MIT License - See [LICENSE](https://github.com/allenchou/cachetic/blob/main/LICENSE) for details.
