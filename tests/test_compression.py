import pathlib
from pprint import pformat

import pydantic

from cachetic import Cachetic


class Person(pydantic.BaseModel):
    name: str
    age: int


def test_compression_enabled(temp_cache_url: pathlib.Path):
    """Test that compression works when enabled."""
    # Create cache with compression enabled
    cache = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url=temp_cache_url,
        compression=True,
    )

    person = Person(name="Alice", age=30)
    cache.set("user:1", person)

    result = cache.get("user:1")
    assert result is not None
    assert result.name == "Alice"
    assert result.age == 30
    assert isinstance(result, Person)


def test_compression_disabled(temp_cache_url: pathlib.Path):
    """Test that compression can be disabled."""
    # Create cache with compression disabled (default)
    cache = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url=temp_cache_url,
        compression=False,
    )

    person = Person(name="Bob", age=25)
    cache.set("user:2", person)

    result = cache.get("user:2")
    assert result is not None
    assert result.name == "Bob"
    assert result.age == 25
    assert isinstance(result, Person)


def test_read_compressed_data(temp_cache_url: pathlib.Path):
    # Create cache with compression disabled
    cache_without_compression = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url=temp_cache_url,
        # compression=False,  # default is False
    )
    # Create cache with compression enabled
    cache_with_compression = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url=temp_cache_url,
        compression=True,
    )

    # Set by compression disabled cache
    person = Person(name="Alice", age=30)
    cache_without_compression.set("user:3", person)
    # Get by both caches
    result = cache_without_compression.get("user:3")
    assert result is not None
    assert pformat(person.model_dump()) == pformat(result.model_dump())
    result = cache_with_compression.get("user:3")
    assert result is not None
    assert pformat(person.model_dump()) == pformat(result.model_dump())

    # Set by compression enabled cache
    person = Person(name="Bob", age=25)
    cache_with_compression.set("user:3", person)
    # Get by both caches
    result = cache_with_compression.get("user:3")
    assert result is not None
    assert pformat(person.model_dump()) == pformat(result.model_dump())
    result = cache_without_compression.get(
        "user:3"
    )  # The disabled cache still can try to decompress the data
    assert result is not None
    assert pformat(person.model_dump()) == pformat(result.model_dump())
