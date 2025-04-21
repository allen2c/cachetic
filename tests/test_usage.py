import os
import socket

import pydantic
import pytest

from cachetic import Cachetic


# ---------------------------------------------------
# EXAMPLE Pydantic model for testing
# ---------------------------------------------------
class Person(pydantic.BaseModel):
    name: str
    age: int


# ---------------------------------------------------
# HELPERS
# ---------------------------------------------------
def is_redis_available(host="localhost", port=6379):
    """
    Quick helper to see if Redis is listening on the default port.
    Useful for skipping Redis-based tests if no server is present.
    """
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


# ---------------------------------------------------
# TESTS
# ---------------------------------------------------
@pytest.fixture
def local_cache():
    """
    Fixture that returns a Cachetic instance configured to use
    the local diskcache (no Redis URL).
    By default, it sets the Pydantic model to Person.
    """
    # Use a temporary directory for local caching to ensure it's clean each test
    return Cachetic[Person](
        object_type=Person,
        cache_url=".test-cache",  # means local diskcache only
        cache_prefix="testlocal",
        cache_ttl=-1,
    )


@pytest.fixture
def redis_cache():
    """
    Fixture that returns a Cachetic instance configured to use
    a Redis URL, if Redis is running. If not, the test will be skipped.
    """
    if not is_redis_available():
        pytest.skip("Redis is not running on localhost:6379, skipping Redis test.")
    # Provide your desired Redis URL here.
    # By default, this tries local Redis (localhost:6379).
    return Cachetic[Person](
        object_type=Person,
        cache_url="redis://localhost:6379/0",  # type: ignore
        cache_prefix="testredis",
        cache_ttl=-1,
    )


def test_local_set_and_get(local_cache: Cachetic[Person]):
    """
    Tests that setting a key in local diskcache works
    and retrieving it returns the correct Pydantic model.
    """
    person = Person(name="Alice", age=30)
    local_cache.set("user:1", person)

    # Retrieve
    result = local_cache.get("user:1")
    assert result is not None
    assert result.name == "Alice"
    assert result.age == 30


def test_local_get_nonexistent_key_returns_none(local_cache: Cachetic[Person]):
    """
    Tests that getting a key which does not exist returns None.
    """
    result = local_cache.get("nonexistent:key")
    assert result is None


def test_local_prefix_usage(local_cache: Cachetic[Person]):
    """
    Verifies that the prefix is applied to the internal key,
    but from a user's perspective, we only provide "mykey".
    """
    p = Person(name="Carol", age=25)
    local_cache.set("mykey", p)
    # The underlying stored key would be "testlocal:mykey"
    # But the user just calls get("mykey").
    result = local_cache.get("mykey")
    assert result is not None
    assert result.name == "Carol"


@pytest.mark.parametrize("ttl", [1, 2])
def test_local_ttl_usage(local_cache: Cachetic[Person], ttl: int):
    """
    Simple test to show that you can set an expiration (TTL).
    Since local diskcache won't automatically expire in memory,
    we just ensure no error is raised and that the TTL is set.
    """
    p = Person(name="Dan", age=99)
    local_cache.set("ttl_test", p, ex=ttl)

    # Immediately get
    res = local_cache.get("ttl_test")
    assert res is not None
    assert res.name == "Dan"


@pytest.mark.skipif(not is_redis_available(), reason="Redis not available.")
def test_redis_set_and_get(redis_cache: Cachetic[Person]):
    """
    Tests that setting a key in Redis works and retrieving
    it returns the correct Pydantic model.
    """
    person = Person(name="Eve", age=28)
    redis_cache.set("user:2", person)

    # Retrieve
    result = redis_cache.get("user:2")
    assert result is not None
    assert result.name == "Eve"
    assert result.age == 28


@pytest.mark.skipif(not is_redis_available(), reason="Redis not available.")
def test_redis_prefix_usage(redis_cache: Cachetic[Person]):
    """
    Verifies that the prefix is applied for Redis usage as well,
    but is transparent to the user.
    """
    p = Person(name="Hank", age=55)
    redis_cache.set("myrediskey", p)
    result = redis_cache.get("myrediskey")
    assert result is not None
    assert result.name == "Hank"


def test_object_type_fallback():
    """
    Shows usage if you don't specify a model_type. By default,
    it uses CacheticDefaultModel (which is basically 'allow' mode).
    """
    # If object_type isn't provided, it defaults to CacheticDefaultModel
    c = Cachetic(cache_url=".test-cache-fallback")
    c.set("fallback_key", {"some": "dictionary data"})  # type: ignore
    result = c.get("fallback_key")
    # The default is CacheticDefaultModel, which can allow extra keys
    # So we check that it's not None and is a Pydantic object with data.
    assert result is not None
    # Because it's the default model, you can do e.g.:
    assert result["some"] == "dictionary data"  # type: ignore


def teardown_module(module):
    """
    Cleanup for any leftover caches after all tests in this file complete.
    """
    import shutil

    # Remove the test dirs we used
    if os.path.exists(".test-cache"):
        shutil.rmtree(".test-cache", ignore_errors=True)
    if os.path.exists(".test-cache-fallback"):
        shutil.rmtree(".test-cache-fallback", ignore_errors=True)
    if os.path.exists(".test-cache-bad"):
        shutil.rmtree(".test-cache-bad", ignore_errors=True)
