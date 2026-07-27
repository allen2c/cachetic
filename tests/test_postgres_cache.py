import time
from pprint import pformat
from unittest.mock import patch

import peewee
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


def _describe_table(db: peewee.PostgresqlDatabase, table: str) -> list[tuple]:
    """Returns (column, type, nullability, position) rows from information_schema."""
    cursor = db.execute_sql(
        "SELECT column_name, data_type, is_nullable, ordinal_position "
        "FROM information_schema.columns WHERE table_name = %s "
        "ORDER BY ordinal_position",
        (table,),
    )
    return list(cursor.fetchall())


def test_async_ddl_matches_sync_ddl(postgres_connection_string: str):
    """The async backend must create exactly the table peewee creates.

    Both sides use CREATE TABLE IF NOT EXISTS, so whichever connects first wins
    and a mismatch diverges silently instead of raising. Comparing the realised
    schemas is the only thing that catches it.
    """
    import asyncio

    from cachetic.aio import close_all
    from cachetic.extensions.aio.postgres import AsyncPostgresCache

    _clear_postgres_registries()
    sync_table = "ddl_check_sync"
    async_table = "ddl_check_async"

    sync_url = postgres_connection_string.replace(
        "table=test_cache", f"table={sync_table}"
    )
    async_url = postgres_connection_string.replace(
        "table=test_cache", f"table={async_table}"
    )

    try:
        sync_cache = PostgresCache(sync_url)
        db = sync_cache._db
        db.execute_sql(f'DROP TABLE IF EXISTS "{sync_table}"')
        db.execute_sql(f'DROP TABLE IF EXISTS "{async_table}"')
        _clear_postgres_registries()

        # Recreate the sync table through peewee.
        sync_cache = PostgresCache(sync_url)

        async def build_async_table() -> None:
            cache = AsyncPostgresCache(async_url)
            await cache._ensure_table()
            await close_all()

        asyncio.run(build_async_table())

        sync_schema = _describe_table(db, sync_table)
        async_schema = _describe_table(db, async_table)

        assert sync_schema, "sync table was not created"
        assert async_schema == sync_schema
        assert [row[0] for row in async_schema] == ["name", "value", "expires_at"]
        assert [row[2] for row in async_schema] == ["NO", "NO", "YES"]
    finally:
        db = PostgresCache(sync_url)._db
        db.execute_sql(f'DROP TABLE IF EXISTS "{sync_table}"')
        db.execute_sql(f'DROP TABLE IF EXISTS "{async_table}"')
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
