"""Lazy expiry cleanup must never clobber a concurrent write — every path.

MongoDB and PostgreSQL have no server-side TTL here: `get` and `exists` read the
row, notice the deadline has passed, and delete it. That delete is conditional on
the deadline they just read, because a `set` landing in between replaces the
entry and its value must not be dropped by the cleanup. `docs/architecture.md`
lists this under "Known trade-offs".

`tests/test_mongo_cache.py` and `tests/test_postgres_cache.py` each pin it for
the **sync** backend's **`get`**. That left four paths through the same
invariant unpinned, and the gap was not symmetric:

* Mongo's `exists` does not share `get`'s fetch — it repeats the find, the
  deadline compare and the conditional delete. A bug in that copy is invisible
  to the `get` test.
* Postgres' `exists` does share `get`'s `_fetch`, so it was covered by accident.
  Pinned here anyway: the sharing is the thing that makes it safe, and nothing
  else stops someone inlining it.
* Neither async backend was exercised at all, on either operation, though both
  carry their own copy of the logic.
"""

import asyncio
import time
import typing
from unittest.mock import patch

import psycopg
import pymongo.collection
import pytest
from pymongo.asynchronous.collection import AsyncCollection

from cachetic.extensions.aio._registry import close_all as aio_close_all
from cachetic.extensions.aio.mongodb import AsyncMongoCache
from cachetic.extensions.aio.postgres import AsyncPostgresCache
from cachetic.extensions.mongodb import MongoCache
from cachetic.extensions.postgres import PostgresCache

Operation = typing.Literal["get", "exists"]

OPERATIONS: list[Operation] = ["get", "exists"]


def _assert_missing(result: typing.Any, operation: Operation) -> None:
    """An expired entry reads as absent, whichever operation asked."""
    assert result is None if operation == "get" else result is False


# --- MongoDB ---------------------------------------------------------------


@pytest.mark.parametrize("operation", OPERATIONS)
def test_sync_mongo_expiry_cleanup_spares_a_concurrent_write(mongo_connection_string: str, operation: Operation):
    key = f"expiry_race_sync_{operation}"
    backend = MongoCache(mongo_connection_string)
    try:
        backend.set(key, b"stale", 1)
        time.sleep(2)

        original_find_one = pymongo.collection.Collection.find_one
        rewritten = False

        def find_one_then_rewrite(self, *args, **kwargs):
            """Simulates a concurrent set() observed after the expiry read."""
            nonlocal rewritten
            doc = original_find_one(self, *args, **kwargs)
            if not rewritten and doc is not None and doc.get("ex") is not None:
                rewritten = True
                MongoCache(mongo_connection_string).set(key, b"fresh", -1)
            return doc

        with patch.object(pymongo.collection.Collection, "find_one", find_one_then_rewrite):
            _assert_missing(getattr(backend, operation)(key), operation)

        assert rewritten, "the simulated concurrent write never ran"
        assert backend.get(key) == b"fresh"
    finally:
        backend.delete(key)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_async_mongo_expiry_cleanup_spares_a_concurrent_write(mongo_connection_string: str, operation: Operation):
    key = f"expiry_race_async_{operation}"

    async def scenario() -> None:
        backend = AsyncMongoCache(mongo_connection_string)
        try:
            await backend.set(key, b"stale", 1)
            await asyncio.sleep(2)

            original_find_one = AsyncCollection.find_one
            rewritten = False

            async def find_one_then_rewrite(self, *args, **kwargs):
                """Simulates a concurrent set() observed after the expiry read."""
                nonlocal rewritten
                doc = await original_find_one(self, *args, **kwargs)
                if not rewritten and doc is not None and doc.get("ex") is not None:
                    rewritten = True
                    await AsyncMongoCache(mongo_connection_string).set(key, b"fresh", -1)
                return doc

            with patch.object(AsyncCollection, "find_one", find_one_then_rewrite):
                _assert_missing(await getattr(backend, operation)(key), operation)

            assert rewritten, "the simulated concurrent write never ran"
            assert await backend.get(key) == b"fresh"
        finally:
            await backend.delete(key)
            await aio_close_all()

    asyncio.run(scenario())


# --- PostgreSQL ------------------------------------------------------------


@pytest.mark.parametrize("operation", OPERATIONS)
def test_sync_postgres_expiry_cleanup_spares_a_concurrent_write(postgres_connection_string: str, operation: Operation):
    key = f"expiry_race_sync_{operation}"
    backend = PostgresCache(postgres_connection_string)
    try:
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
            _assert_missing(getattr(backend, operation)(key), operation)

        assert rewritten, "the simulated concurrent write never ran"
        assert backend.get(key) == b"fresh"
    finally:
        backend.delete(key)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_async_postgres_expiry_cleanup_spares_a_concurrent_write(postgres_connection_string: str, operation: Operation):
    key = f"expiry_race_async_{operation}"

    async def scenario() -> None:
        backend = AsyncPostgresCache(postgres_connection_string)
        try:
            await backend.set(key, b"stale", 1)
            await asyncio.sleep(2)

            original_fetchone = psycopg.AsyncCursor.fetchone
            rewritten = False

            async def fetchone_then_rewrite(self):
                nonlocal rewritten
                row = await original_fetchone(self)
                if not rewritten and row is not None and row[1] is not None:
                    rewritten = True
                    await AsyncPostgresCache(postgres_connection_string).set(key, b"fresh", -1)
                return row

            with patch.object(psycopg.AsyncCursor, "fetchone", fetchone_then_rewrite):
                _assert_missing(await getattr(backend, operation)(key), operation)

            assert rewritten, "the simulated concurrent write never ran"
            assert await backend.get(key) == b"fresh"
        finally:
            await backend.delete(key)
            await aio_close_all()

    asyncio.run(scenario())
