# tests/test_readme_usages.py

import pathlib
import socket
from typing import Dict, List

import pydantic
import pytest

from cachetic import CacheNotFoundError, Cachetic


class Person(pydantic.BaseModel):
    name: str
    age: int


def is_redis_available(host="localhost", port=6379) -> bool:
    """Check if Redis is available for testing."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


class TestBasicUsage:
    """Test the basic usage example from README."""

    def test_basic_usage_example(self, temp_cache_url: pathlib.Path):
        """Test the basic usage example exactly as shown in README."""
        # Create cache instance
        cache = Cachetic[Person](
            object_type=pydantic.TypeAdapter(Person),
            cache_url=temp_cache_url,  # Using temp dir instead of ".cache"
        )

        # Store and retrieve
        person = Person(name="Alice", age=30)
        cache.set("user:1", person)

        result = cache.get("user:1")
        assert result is not None
        assert result.name == "Alice"
        assert result.age == 30
        assert isinstance(result, Person)


class TestRedisBackend:
    """Test Redis backend examples from README."""

    @pytest.mark.skipif(not is_redis_available(), reason="Redis not available")
    def test_redis_backend_example(self):
        """Test Redis backend example from README."""
        cache = Cachetic[Person](
            object_type=pydantic.TypeAdapter(Person),
            cache_url="redis://localhost:6379/0",
        )

        # Store and retrieve
        person = Person(name="Bob", age=25)
        cache.set("redis_user:1", person)

        result = cache.get("redis_user:1")
        assert result is not None
        assert result.name == "Bob"
        assert result.age == 25
        assert isinstance(result, Person)

        # Cleanup
        cache.cache.delete(cache.get_cache_key("redis_user:1"))


class TestPrimitiveTypes:
    """Test primitive type examples from README."""

    def test_string_cache_example(self, temp_cache_url: pathlib.Path):
        """Test string cache example from README."""
        # String cache
        str_cache = Cachetic[str](
            object_type=pydantic.TypeAdapter(str), cache_url=temp_cache_url
        )

        str_cache.set("greeting", "Hello, World!")
        result = str_cache.get("greeting")
        assert result == "Hello, World!"
        assert isinstance(result, str)

    def test_list_cache_example(self, temp_cache_url: pathlib.Path):
        """Test list cache example from README."""
        # List cache
        list_cache = Cachetic[list[str]](
            object_type=pydantic.TypeAdapter(list[str]), cache_url=temp_cache_url
        )

        list_cache.set("items", ["apple", "banana", "cherry"])
        result = list_cache.get("items")
        assert result == ["apple", "banana", "cherry"]
        assert isinstance(result, list)
        assert all(isinstance(item, str) for item in result)


class TestComplexTypes:
    """Test complex type examples from README."""

    def test_dictionary_cache_example(self, temp_cache_url: pathlib.Path):
        """Test dictionary cache example from README."""
        # Dictionary cache
        data = {"users": [{"id": 1, "name": "Alice"}], "total": 1}
        dict_cache = Cachetic[Dict](
            object_type=pydantic.TypeAdapter(Dict), cache_url=temp_cache_url
        )

        dict_cache.set("user_data", data)
        result = dict_cache.get("user_data")
        assert result == data
        assert isinstance(result, dict)
        assert result["total"] == 1
        assert result["users"][0]["name"] == "Alice"

    def test_list_of_models_example(self, temp_cache_url: pathlib.Path):
        """Test list of models example from README."""
        # List of models
        people_cache = Cachetic[List[Person]](
            object_type=pydantic.TypeAdapter(List[Person]), cache_url=temp_cache_url
        )

        people = [Person(name="Alice", age=30), Person(name="Bob", age=25)]
        people_cache.set("team", people)

        result = people_cache.get("team")
        assert result == people
        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(person, Person) for person in result)
        assert result[0].name == "Alice"
        assert result[1].name == "Bob"


class TestTTLExamples:
    """Test TTL configuration examples from README."""

    def test_no_expiration_example(self, temp_cache_url: pathlib.Path):
        """Test no expiration (default) example from README."""
        # No expiration (default)
        cache = Cachetic[str](
            object_type=pydantic.TypeAdapter(str),
            cache_url=temp_cache_url,
            default_ttl=-1,
        )

        cache.set("persistent", "value")
        result = cache.get("persistent")
        assert result == "value"

    def test_hour_expiration_example(self, temp_cache_url: pathlib.Path):
        """Test 1 hour expiration example from README."""
        # 1 hour expiration
        cache = Cachetic[str](
            object_type=pydantic.TypeAdapter(str),
            cache_url=temp_cache_url,
            default_ttl=3600,
        )

        cache.set("hourly", "value")
        result = cache.get("hourly")
        assert result == "value"

    def test_per_operation_ttl_example(self, temp_cache_url: pathlib.Path):
        """Test per-operation TTL example from README."""
        cache = Cachetic[str](
            object_type=pydantic.TypeAdapter(str), cache_url=temp_cache_url
        )

        # Per-operation TTL
        cache.set("key", "value", ex=300)  # 5 minutes
        result = cache.get("key")
        assert result == "value"

    def test_zero_ttl_disables_cache(self, temp_cache_url: pathlib.Path):
        """Test that TTL=0 disables caching."""
        cache = Cachetic[str](
            object_type=pydantic.TypeAdapter(str),
            cache_url=temp_cache_url,
            default_ttl=0,
        )

        # Should not cache anything
        cache.set("disabled", "value")
        result = cache.get("disabled")
        assert result is None  # Should not be cached


class TestErrorHandling:
    """Test error handling examples from README."""

    def test_get_returns_none_example(self, temp_cache_url: pathlib.Path):
        """Test that get() returns None for missing keys as shown in README."""
        cache = Cachetic[str](
            object_type=pydantic.TypeAdapter(str), cache_url=temp_cache_url
        )

        # get() returns None for missing keys
        result = cache.get("nonexistent")
        assert result is None

    def test_get_or_raise_throws_exception_example(self, temp_cache_url: pathlib.Path):
        """Test get_or_raise() throws exception as shown in README."""
        cache = Cachetic[str](
            object_type=pydantic.TypeAdapter(str), cache_url=temp_cache_url
        )

        # get_or_raise() throws exception
        with pytest.raises(CacheNotFoundError):
            cache.get_or_raise("nonexistent")


class TestEnvironmentVariables:
    """Test environment variable configuration."""

    def test_prefix_configuration(self, temp_cache_url: pathlib.Path):
        """Test prefix configuration works as documented."""
        cache = Cachetic[str](
            object_type=pydantic.TypeAdapter(str),
            cache_url=temp_cache_url,
            prefix="myapp",
        )

        cache.set("key", "value")

        # Check that the actual key has the prefix
        cache_key = cache.get_cache_key("key", with_prefix=True)
        assert cache_key == "myapp:key"

        # But retrieval still works with the original key
        result = cache.get("key")
        assert result == "value"


class TestWorkingExamples:
    """Additional tests to ensure all examples work in practice."""

    def test_multiple_cache_instances(self, temp_cache_url: pathlib.Path):
        """Test that multiple cache instances work independently."""
        str_cache = Cachetic[str](
            object_type=pydantic.TypeAdapter(str), cache_url=temp_cache_url / "strings"
        )

        person_cache = Cachetic[Person](
            object_type=pydantic.TypeAdapter(Person),
            cache_url=temp_cache_url / "people",
        )

        # Store different types
        str_cache.set("message", "Hello")
        person_cache.set("user", Person(name="Charlie", age=35))

        # Retrieve and verify
        assert str_cache.get("message") == "Hello"
        user = person_cache.get("user")
        assert user is not None
        assert user.name == "Charlie"
        assert user.age == 35

    def test_complex_nested_data(self, temp_cache_url: pathlib.Path):
        """Test caching complex nested data structures."""
        complex_data = {
            "metadata": {"version": "1.0", "created": "2024-01-01"},
            "users": [
                {"id": 1, "profile": {"name": "Alice", "settings": {"theme": "dark"}}},
                {"id": 2, "profile": {"name": "Bob", "settings": {"theme": "light"}}},
            ],
            "stats": {"total_users": 2, "active_sessions": [1, 2]},
        }

        cache = Cachetic[dict](
            object_type=pydantic.TypeAdapter(dict), cache_url=temp_cache_url
        )

        cache.set("complex_data", complex_data)
        result = cache.get("complex_data")

        assert result is not None
        assert result == complex_data
        assert result["metadata"]["version"] == "1.0"
        assert len(result["users"]) == 2
        assert result["users"][0]["profile"]["name"] == "Alice"
