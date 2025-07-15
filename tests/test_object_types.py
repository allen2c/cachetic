# tests/test_object_types.py

import pathlib
import typing

import pydantic

from cachetic import Cachetic


class Person(pydantic.BaseModel):
    name: str
    age: int


def test_bytes(temp_cache_url: pathlib.Path):
    """Tests caching bytes."""
    # Use a unique cache dir per test to avoid conflicts
    cache = Cachetic[bytes](
        object_type=pydantic.TypeAdapter(bytes),
        cache_url=temp_cache_url,
    )
    key = "test_bytes"
    value = b"some bytes"
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, bytes)


def test_str(temp_cache_url: pathlib.Path):
    """Tests caching strings."""
    cache = Cachetic[str](
        object_type=pydantic.TypeAdapter(str),
        cache_url=temp_cache_url,
    )
    key = "test_str"
    value = "some string"
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, str)


def test_int(temp_cache_url: pathlib.Path):
    """Tests caching integers."""
    cache = Cachetic[int](
        object_type=pydantic.TypeAdapter(int),
        cache_url=temp_cache_url,
    )
    key = "test_int"
    value = 12345
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, int)


def test_float(temp_cache_url: pathlib.Path):
    """Tests caching floats."""
    cache = Cachetic[float](
        object_type=pydantic.TypeAdapter(float),
        cache_url=temp_cache_url,
    )
    key = "test_float"
    value = 123.45
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, float)


def test_bool(temp_cache_url: pathlib.Path):
    """Tests caching booleans."""
    cache = Cachetic[bool](
        object_type=pydantic.TypeAdapter(bool),
        cache_url=temp_cache_url,
    )
    key_true = "test_bool_true"
    key_false = "test_bool_false"
    cache.set(key_true, True)
    cache.set(key_false, False)
    assert cache.get(key_true) is True
    assert cache.get(key_false) is False


def test_list(temp_cache_url: pathlib.Path):
    """Tests caching lists."""
    cache = Cachetic[list](
        object_type=pydantic.TypeAdapter(list),
        cache_url=temp_cache_url,
    )
    key = "test_list"
    value = [1, "two", 3.0, False, {"nested": "dict"}]
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, list)


def test_dict(temp_cache_url: pathlib.Path):
    """Tests caching dictionaries."""
    cache = Cachetic[dict](
        object_type=pydantic.TypeAdapter(dict),
        cache_url=temp_cache_url,
    )
    key = "test_dict"
    value = {"a": 1, "b": "string", "c": [1, 2], "d": True}
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, dict)


def test_pydantic_model(temp_cache_url: pathlib.Path):
    """Tests caching Pydantic BaseModel instances."""
    cache = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url=temp_cache_url,
    )
    key = "test_pydantic_model"
    value = Person(name="Alice", age=30)
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, Person)
    assert retrieved.name == "Alice"
    assert retrieved.age == 30


def test_pydantic_models(temp_cache_url: pathlib.Path):
    """Tests caching lists of Pydantic models."""
    cache = Cachetic[typing.List[Person]](
        object_type=pydantic.TypeAdapter(typing.List[Person]),
        cache_url=temp_cache_url,
    )
    key = "test_pydantic_models"
    value = [Person(name="Alice", age=30), Person(name="Bob", age=25)]
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, list)
    assert len(retrieved) == 2
    assert retrieved[0] == value[0]
    assert retrieved[1] == value[1]
