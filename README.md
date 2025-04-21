# cachetic

[![PyPI version](https://img.shields.io/pypi/v/cachetic.svg)](https://pypi.org/project/cachetic/)
[![Python Version](https://img.shields.io/pypi/pyversions/cachetic.svg)](https://pypi.org/project/cachetic/)
[![License](https://img.shields.io/pypi/l/cachetic.svg)](https://opensource.org/licenses/MIT)

Simple cache with pydantic[cite: 12]. This library integrates [Pydantic](https://docs.pydantic.dev/) models with either a local filesystem cache ([diskcache](https://pypi.org/project/diskcache/)) or a Redis cache[cite: 13]. Designed for Python 3.11+[cite: 12, 13].

## Features

* **Pydantic Integration**: Seamlessly cache Pydantic `BaseModel` instances or use `TypeAdapter` for complex types[cite: 71, 72, 110].
* **Flexible Backends**: Supports local filesystem caching via `diskcache` or distributed caching with Redis[cite: 13, 98, 102].
* **Type Support**: Caches various Python types including primitives (`str`, `int`, `bytes`, `float`, `bool`), collections (`list`, `dict`), and arbitrary pickleable `object`s[cite: 67, 68, 69, 70, 73, 92].
* **Configurable**: Set cache TTL, key prefixes, and backend location easily[cite: 94, 97].

## Installation

Install via **pip**:

```bash
pip install cachetic
````

Or via **Poetry**:

```bash
poetry add cachetic
```

## Quick Start

### Basic Example (Local Disk Cache)

```python
from pydantic import BaseModel
from cachetic import Cachetic

class Person(BaseModel):
    name: str
    age: int

# Create a Cachetic instance using diskcache
# Defaults to './.cache' if cache_url is omitted
cache = Cachetic[Person](object_type=Person, cache_prefix="myapp")

# Store a model instance
alice = Person(name="Alice", age=30)
cache.set("user:1", alice)

# Retrieve the model instance
retrieved_person = cache.get("user:1")
if retrieved_person:
    print(f"Retrieved: {retrieved_person.name}, Age: {retrieved_person.age}") # Output: Retrieved: Alice, Age: 30 [cite: 14]
```

### Using Redis

Provide a Redis connection URL to use Redis as the backend:

```python
# Assumes Redis is running on redis://localhost:6379/0
redis_cache = Cachetic[Person](
    object_type=Person,
    cache_url="redis://localhost:6379/0", # Your Redis URL [cite: 79]
    cache_prefix="myapp_redis"
)

# Store and retrieve
bob = Person(name="Bob", age=40)
redis_cache.set("user:2", bob)
retrieved_bob = redis_cache.get("user:2")
if retrieved_bob:
    print(f"Retrieved from Redis: {retrieved_bob.name}") # Output: Retrieved from Redis: Bob [cite: 14]
```

### Caching Other Types (e.g., a List of Dicts)

```python
from typing import List, Dict

# Use TypeAdapter for complex non-BaseModel types
DictList = List[Dict[str, str]]
list_cache = Cachetic[DictList](object_type=DictList) # Or use pydantic.TypeAdapter(DictList)

data_list = [{"item": "apple"}, {"item": "banana"}]
list_cache.set("items", data_list)

retrieved_list = list_cache.get("items")
if retrieved_list:
    print(f"Retrieved list: {retrieved_list}") # Output: Retrieved list: [{'item': 'apple'}, {'item': 'banana'}]
```

## Configuration

`Cachetic` can be configured via environment variables (with `CACHETIC_` prefix) or directly during instantiation. Key parameters include:

* **`object_type`**: The type of object being cached (e.g., `Person`, `list`, `bytes`, `pydantic.TypeAdapter(...)`). Defaults to `object` for general pickling[cite: 93].
* **`cache_url`**: The Redis connection URL (`redis://...`) or local directory path for `diskcache`[cite: 93]. Defaults to `./.cache`[cite: 93].
* **`cache_ttl`**: Default time-to-live for cache entries in seconds[cite: 94].
    * `-1` (default): No expiration[cite: 95].
    * `0`: Disable caching [writes have no effect](cite: 96, 116).
    * `>0`: Expire after N seconds[cite: 97].
* **`cache_prefix`**: A string prefix added to all cache keys[cite: 97].

## Development

1. **Clone the repository.**
2. **Install dependencies** (including development tools):
    ```bash
    poetry install --all-extras # Or use 'make install-all' [cite: 11]
    ```
3. **Run tests**:
    ```bash
    make test # Uses pytest [cite: 11, 19]
    ```
4. **Format code**:
    ```bash
    make format-all # Runs isort and black [cite: 9, 10, 11]
    ```

## License

This project is licensed under the MIT License - see the [LICENSE](https://www.google.com/search?q=LICENSE) file for details[cite: 1, 12].
