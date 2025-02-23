# cachetic

A simple cache library that integrates [Pydantic](https://docs.pydantic.dev/) models with either a local filesystem cache ([diskcache](https://pypi.org/project/diskcache/)) or a Redis cache. Designed for Python 3.11+.

## Installation

Install via **pip**:

```bash
pip install cachetic
```

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

# Create a Cachetic instance that uses diskcache locally
local_cache = Cachetic[Person](
    object_type=Person,
    cache_url=None,   # No Redis URL -> use local disk cache
    cache_dir=".my-cache",  # Directory for local cache files
    cache_prefix="myprefix"  # Optional string to prefix all keys
)

# Store a model
alice = Person(name="Alice", age=30)
local_cache.set("user:1", alice)

# Retrieve the model
person = local_cache.get("user:1")
if person:
    print(person.name, person.age)  # "Alice", 30
```

### Using Redis

If you have a Redis server running, you can provide the URL to Cachetic:

```python
redis_cache = Cachetic[Person](
    object_type=Person,
    cache_url="redis://localhost:6379/0",  # Adjust as needed
    cache_prefix="myprefix"
)

# Store and retrieve
redis_cache.set("user:2", Person(name="Bob", age=40))
bob = redis_cache.get("user:2")
if bob:
    print(bob.name, bob.age)  # "Bob", 40
```

### Storing and Retrieving Lists of Models

```python
people_list = [
    Person(name="Charlie", age=25),
    Person(name="Diana", age=32),
]

local_cache.set_objects("group:1", people_list)
retrieved_people = local_cache.get_objects("group:1")

for p in retrieved_people:
    print(p.name, p.age)
```

### Fallback (No Explicit Model)

If you don’t provide an `object_type`, Cachetic defaults to a permissive [Pydantic model](cachetic/__init__.py) that allows extra keys. This can be handy if you want quick-and-dirty caching without strict validation:

```python
# Fallback usage (stores any JSON-serializable data)
generic_cache = Cachetic()
generic_cache.set("generic_key", {"arbitrary": "data"})
data = generic_cache.get("generic_key")
print(data.arbitrary)  # 'data'
```

## Configuration

- **`cache_url`**: The URL for your Redis server (`redis://...`). If omitted or `None`, Cachetic uses diskcache locally.
- **`cache_dir`**: Directory path for diskcache files (default: `./.cache`).
- **`cache_prefix`**: Optional string prefix applied to every key in the cache.
- **`cache_ttl`**: Default TTL (time-to-live). Set to `-1` for no expiration.

## Testing

After cloning the repository, install development dependencies and run the tests:

```bash
poetry install --all-extras --all-groups
make test
```
