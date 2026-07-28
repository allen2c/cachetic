import asyncio
import time
from pprint import pformat
from unittest.mock import patch

import psycopg
import pydantic
from psycopg import sql

from cachetic import Cachetic
from cachetic.extensions import _registry
from cachetic.extensions.postgres import PostgresCache


class Person(pydantic.BaseModel):
    name: str
    age: int


def _clear_postgres_registries() -> None:
    """Closes and forgets every shared client, for test isolation."""
    _registry.close_all()


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


def test_same_url_shares_pool(postgres_connection_string: str):
    """Same connection URL should reuse the same connection pool."""
    _clear_postgres_registries()
    try:
        a = PostgresCache(postgres_connection_string)
        b = PostgresCache(postgres_connection_string)
        assert a._entry() is b._entry()
        assert _registry._registry_size() == 1
    finally:
        _clear_postgres_registries()


def test_different_table_shares_pool(postgres_connection_string: str):
    """Different tables on the same host should share a connection pool."""
    _clear_postgres_registries()
    url_a = postgres_connection_string
    url_b = postgres_connection_string.replace("table=test_cache", "table=other_cache")
    try:
        a = PostgresCache(url_a)
        b = PostgresCache(url_b)
        assert a._entry() is b._entry()
        assert _registry._registry_size() == 1
    finally:
        _clear_postgres_registries()


def _describe_table(cache: PostgresCache, table: str) -> list[tuple]:
    """Returns (column, type, nullability, position) rows from information_schema."""
    with cache._ready_pool().connection() as conn:
        return conn.execute(
            "SELECT column_name, data_type, is_nullable, ordinal_position "
            "FROM information_schema.columns WHERE table_name = %s "
            "ORDER BY ordinal_position",
            (table,),
        ).fetchall()


def _drop_tables(cache: PostgresCache, *tables: str) -> None:
    with cache._ready_pool().connection() as conn:
        for table in tables:
            conn.execute(
                sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table))
            )


def test_sync_and_async_share_one_ddl_definition():
    """Neither backend may carry its own copy of the schema."""
    from cachetic.extensions import _postgres_sql
    from cachetic.extensions import postgres as sync_postgres
    from cachetic.extensions.aio import postgres as aio_postgres

    assert aio_postgres._postgres_sql is _postgres_sql
    assert sync_postgres._postgres_sql is _postgres_sql


def test_async_ddl_matches_sync_ddl(postgres_connection_string: str):
    """The two backends must realise byte-identical tables.

    Both use CREATE TABLE IF NOT EXISTS, so whichever connects first wins and a
    mismatch would diverge silently instead of raising. They now compose the
    statement from one shared constant, and this checks the schema that reaches
    the server actually matches.
    """
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
        _drop_tables(PostgresCache(sync_url), sync_table, async_table)
        _clear_postgres_registries()

        sync_cache = PostgresCache(sync_url)

        async def build_async_table() -> None:
            cache = AsyncPostgresCache(async_url)
            await cache._ready_pool()
            await close_all()

        asyncio.run(build_async_table())

        sync_schema = _describe_table(sync_cache, sync_table)
        async_schema = _describe_table(sync_cache, async_table)

        assert sync_schema, "sync table was not created"
        assert async_schema == sync_schema
        assert [row[0] for row in async_schema] == ["name", "value", "expires_at"]
        assert [row[2] for row in async_schema] == ["NO", "NO", "YES"]
    finally:
        _drop_tables(PostgresCache(sync_url), sync_table, async_table)
        _clear_postgres_registries()


def test_table_created_once_per_pair(postgres_connection_string: str):
    """The DDL is issued once per (db_url, table), not on every operation."""
    _clear_postgres_registries()
    try:
        first = PostgresCache(postgres_connection_string)
        first.exists("ddl-probe")
        entry = first._entry()
        assert first._table in entry.ensured

        pool = entry.client
        with patch.object(pool, "connection", wraps=pool.connection) as spy:
            second = PostgresCache(postgres_connection_string)
            second.exists("ddl-probe")
            # One checkout for the SELECT; the DDL is not re-issued.
            assert spy.call_count == 1
    finally:
        _clear_postgres_registries()


# --- Connection-option parity (sync vs async) ---


def _application_name(cache: PostgresCache) -> str:
    with cache._ready_pool().connection() as conn:
        row = conn.execute("SELECT current_setting('application_name')").fetchone()
    assert row is not None
    return row[0]


def test_libpq_options_reach_both_backends(postgres_connection_string: str):
    """Query parameters must configure the connection for sync *and* async.

    The two backends hand the same conninfo string to psycopg rather than each
    re-deriving connection arguments, so an option like ``application_name``
    cannot take effect on one side only.
    """
    from cachetic.aio import close_all
    from cachetic.extensions.aio.postgres import AsyncPostgresCache

    expected = "cachetic-parity-probe"
    url = f"{postgres_connection_string}&application_name={expected}"

    _clear_postgres_registries()
    try:
        assert _application_name(PostgresCache(url)) == expected

        async def async_application_name() -> str:
            cache = AsyncPostgresCache(url)
            pool = await cache._ready_pool()
            async with pool.connection() as conn:
                cursor = await conn.execute(
                    "SELECT current_setting('application_name')"
                )
                row = await cursor.fetchone()
            await close_all()
            assert row is not None
            return row[0]

        assert asyncio.run(async_application_name()) == expected
    finally:
        _clear_postgres_registries()


# --- Pool sizing ---


def test_pool_size_defaults_to_one_connection(postgres_connection_string: str):
    """A cache must not hold a handful of connections open by default.

    psycopg's own default is min_size=4, which multiplies across every process
    in a deployment for a component that is optional by definition.
    """
    _clear_postgres_registries()
    try:
        pool = PostgresCache(postgres_connection_string)._ready_pool()
        assert pool.min_size == 1
        assert pool.max_size > 1, "max_size=min_size would serialise every query"
    finally:
        _clear_postgres_registries()


def test_pool_size_is_configurable_for_both_backends(postgres_connection_string: str):
    """The URL parameters must reach the sync *and* the async pool."""
    from cachetic.aio import close_all
    from cachetic.extensions.aio.postgres import AsyncPostgresCache

    url = f"{postgres_connection_string}&pool_min_size=2&pool_max_size=5"

    _clear_postgres_registries()
    try:
        sync_pool = PostgresCache(url)._ready_pool()
        assert (sync_pool.min_size, sync_pool.max_size) == (2, 5)

        async def async_sizes() -> tuple[int, int]:
            pool = await AsyncPostgresCache(url)._ready_pool()
            sizes = (pool.min_size, pool.max_size)
            await close_all()
            return sizes

        assert asyncio.run(async_sizes()) == (2, 5)
    finally:
        _clear_postgres_registries()


# --- Lazy expiry must not clobber a concurrent write ---


def test_expired_cleanup_does_not_drop_a_concurrent_write(
    postgres_connection_string: str,
):
    """A set() landing between the expiry read and its delete must survive.

    get() reads an expired row and then deletes it. If that delete is not tied
    to the deadline it read, a value written in between is silently lost.
    """
    _clear_postgres_registries()
    key = "expiry_race"
    try:
        backend = PostgresCache(postgres_connection_string)
        backend.set(key, b"stale", 1)
        time.sleep(2)

        original_fetchone = psycopg.Cursor.fetchone
        rewritten = False

        def fetchone_then_rewrite(self):
            """Simulates a concurrent set() observed after the expiry read."""
            nonlocal rewritten
            row = original_fetchone(self)
            if not rewritten and row is not None and row[1] is not None:
                rewritten = True
                PostgresCache(postgres_connection_string).set(key, b"fresh", -1)
            return row

        with patch.object(psycopg.Cursor, "fetchone", fetchone_then_rewrite):
            assert backend.get(key) is None

        assert rewritten, "the simulated concurrent write never ran"
        assert backend.get(key) == b"fresh"
    finally:
        PostgresCache(postgres_connection_string).delete(key)
        _clear_postgres_registries()
