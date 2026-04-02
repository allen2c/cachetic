import time
from pprint import pformat
from unittest.mock import patch

import pydantic

from cachetic import Cachetic
from cachetic.extensions.postgres import (
    PostgresCache,
    _db_registry,
    _ensured_tables,
)


class Person(pydantic.BaseModel):
    name: str
    age: int


def _clear_postgres_registries() -> None:
    """Clears module-level state for test isolation."""
    _db_registry.clear()
    _ensured_tables.clear()


def test_postgres_cache_set_get(postgres_connection_string: str):
    """Full lifecycle: set → get → expire → get → delete."""
    cache = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url=postgres_connection_string,
        default_ttl=15 * 60,
    )
    key = "test_person"
    value = Person(name="Alice", age=30)
    cache.delete(key)
    ex = 2

    result = cache.get(key)
    assert result is None

    cache.set(key, value, ex=ex)
    result = cache.get(key)
    assert result is not None
    assert pformat(result.model_dump()) == pformat(value.model_dump())

    time.sleep(ex + 1)

    result = cache.get(key)
    assert result is None

    cache.set(key, value)
    res = cache.get(key)
    assert res is not None
    assert pformat(res.model_dump()) == pformat(value.model_dump())

    time.sleep(ex + 1)

    res = cache.get(key)
    assert res is not None
    assert pformat(res.model_dump()) == pformat(value.model_dump())

    cache.delete(key)
    res = cache.get(key)
    assert res is None


def test_postgres_exists(postgres_connection_string: str):
    """Tests exists() with lazy expiry."""
    cache = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url=postgres_connection_string,
        default_ttl=-1,
    )
    key = "exist_test"
    value = Person(name="Bob", age=25)
    cache.delete(key)

    assert cache.exists(key) is False

    cache.set(key, value)
    assert cache.exists(key) is True

    cache.set(key, value, ex=1)
    time.sleep(2)
    assert cache.exists(key) is False


# --- Connection reuse tests ---


def test_same_url_shares_db(postgres_connection_string: str):
    """Same connection URL should reuse the same database instance."""
    _clear_postgres_registries()
    try:
        a = PostgresCache(postgres_connection_string)
        b = PostgresCache(postgres_connection_string)
        assert a._db is b._db
        assert len(_db_registry) == 1
    finally:
        _clear_postgres_registries()


def test_different_table_shares_db(postgres_connection_string: str):
    """Different tables on the same host should share a database instance."""
    _clear_postgres_registries()
    url_a = postgres_connection_string
    url_b = postgres_connection_string.replace("table=test_cache", "table=other_cache")
    try:
        a = PostgresCache(url_a)
        b = PostgresCache(url_b)
        assert a._db is b._db
        assert len(_db_registry) == 1
    finally:
        _clear_postgres_registries()


def test_table_created_once_per_pair(postgres_connection_string: str):
    """create_tables should be called once per (db_url, table) pair."""
    _clear_postgres_registries()
    try:
        with patch("peewee.PostgresqlDatabase.create_tables") as mock_ct:
            PostgresCache(postgres_connection_string)
            PostgresCache(postgres_connection_string)
            assert mock_ct.call_count == 1
    finally:
        _clear_postgres_registries()
