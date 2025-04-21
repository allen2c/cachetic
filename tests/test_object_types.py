import pickle
import typing
from threading import RLock  # Unsupported object type, cannot be pickled

import pydantic
import pytest

from cachetic import Cachetic


# Define helper classes at module level for pickling
class SimpleObject:
    """A simple class for testing object pickling."""

    def __init__(self, x):
        self.x = x

    def __eq__(self, other):
        # Ensure comparison works correctly after pickling/unpickling
        return isinstance(other, SimpleObject) and self.x == other.x


# --- Pydantic Models ---
class Person(pydantic.BaseModel):
    name: str
    age: int


# --- Pydantic TypeAdapter ---
# Define the TypeAdapter instance at module level
PeopleAdapter = pydantic.TypeAdapter(typing.List[Person])


def test_bytes():
    """Tests caching bytes."""
    # Use a unique cache dir per test to avoid conflicts
    cache = Cachetic[bytes](object_type=bytes, cache_url="./.cache/.test-cache-bytes")
    key = "test_bytes"
    value = b"some bytes"
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, bytes)


def test_str():
    """Tests caching strings."""
    cache = Cachetic[str](object_type=str, cache_url="./.cache/.test-cache-str")
    key = "test_str"
    value = "some string"
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, str)


def test_int():
    """Tests caching integers."""
    cache = Cachetic[int](object_type=int, cache_url="./.cache/.test-cache-int")
    key = "test_int"
    value = 12345
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, int)


def test_float():
    """Tests caching floats."""
    cache = Cachetic[float](object_type=float, cache_url="./.cache/.test-cache-float")
    key = "test_float"
    value = 123.45
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, float)


def test_bool():
    """Tests caching booleans."""
    cache = Cachetic[bool](object_type=bool, cache_url="./.cache/.test-cache-bool")
    key_true = "test_bool_true"
    key_false = "test_bool_false"
    cache.set(key_true, True)
    cache.set(key_false, False)
    assert cache.get(key_true) is True
    assert cache.get(key_false) is False


def test_list():
    """Tests caching lists (via JSON)."""
    cache = Cachetic[list](object_type=list, cache_url="./.cache/.test-cache-list")
    key = "test_list"
    value = [1, "two", 3.0, False, {"nested": "dict"}]
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, list)


def test_dict():
    """Tests caching dictionaries (via JSON)."""
    cache = Cachetic[dict](object_type=dict, cache_url="./.cache/.test-cache-dict")
    key = "test_dict"
    value = {"a": 1, "b": "string", "c": [1, 2], "d": True}
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, dict)


def test_pydantic_base_model():
    """Tests caching Pydantic BaseModel instances."""
    cache = Cachetic[Person](
        object_type=Person, cache_url="./.cache/.test-cache-pydantic-model"
    )
    key = "test_pydantic_model"
    value = Person(name="Alice", age=30)
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, Person)
    assert retrieved.name == "Alice"
    assert retrieved.age == 30


def test_pydantic_type_adapter():
    """Tests caching objects validated by Pydantic TypeAdapter."""
    # Initialize without generic type when object_type is a TypeAdapter instance
    cache = Cachetic(
        object_type=PeopleAdapter,  # type: ignore
        cache_url="./.cache/.test-cache-pydantic-adapter",
    )
    key = "test_pydantic_adapter"
    value = [Person(name="Bob", age=40), Person(name="Charlie", age=25)]
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, list)
    assert len(retrieved) == 2
    assert isinstance(retrieved[0], Person)
    assert retrieved[0].name == "Bob"
    assert retrieved[1].name == "Charlie"


def test_object():
    """Tests caching arbitrary pickleable Python objects."""
    cache = Cachetic[object](
        object_type=object, cache_url="./.cache/.test-cache-object"
    )
    key = "test_object"
    value = SimpleObject(x="data")  # Use module-level class
    cache.set(key, value)
    retrieved = cache.get(key)
    assert retrieved == value
    assert isinstance(retrieved, SimpleObject)
    assert retrieved.x == "data"


def test_unsupported_set():
    """Tests that attempting to cache an unpickleable object raises an error."""
    cache = Cachetic[object](
        object_type=object, cache_url="./.cache/.test-cache-unsupported-set"
    )
    key = "test_unsupported_set"
    value = RLock()  # RLock instances are not pickleable

    # Use broad Exception first, refine if specific error is consistent
    # pickle.PicklingError inherits Exception, TypeError might also occur
    with pytest.raises((pickle.PicklingError, TypeError)):
        cache.set(key, value)


def test_unsupported_get():
    """Tests that getting data with an unsupported object_type raises ValueError."""
    cache_setter = Cachetic[str](
        object_type=str, cache_url="./.cache/.test-cache-unsupported-get"
    )
    key = "test_unsupported_get"
    cache_setter.set(key, "some data")

    # Create a new instance to get the data
    cache_getter = Cachetic(cache_url="./.cache/.test-cache-unsupported-get")
    # Manually set an invalid object_type not handled in the 'get' method
    cache_getter.object_type = RLock  # type: ignore

    with pytest.raises(ValueError, match="Unsupported object type"):
        cache_getter.get(key)
