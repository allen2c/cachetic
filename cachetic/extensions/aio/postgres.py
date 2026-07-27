"""Async PostgreSQL adapter for AsyncCacheProtocol.

peewee has no async support, so this backend talks to psycopg3 directly through
an ``AsyncConnectionPool``. The table definition below is byte-for-byte what
peewee emits for the sync backend's model — both sides use
``CREATE TABLE IF NOT EXISTS``, so whichever connects first defines the table and
a mismatch would silently diverge rather than raise.

``value`` is stored as ``TEXT`` to match the sync backend. That is safe because
every value written through ``Cachetic`` is a base64 Data URL, hence ASCII.
"""

import logging
import math
import time
import typing

import psycopg
import psycopg_pool
from psycopg import sql

from cachetic.extensions._url import PostgresUrlParts, parse_postgres_url
from cachetic.extensions.aio import _registry
from cachetic.types.async_cache_protocol import AsyncCacheProtocol
from cachetic.utils.hide_url_password import hide_url_password

logger = logging.getLogger(__name__)

_NAMESPACE = "postgres"

# Marks a pool as opened inside Entry.ensured, alongside the table names.
_POOL_OPENED = "\0pool-opened"

# psycopg_pool retries failed connections forever in the background. Without a
# bounded wait, a wrong password or unreachable host hangs the first operation
# instead of raising, so opening waits for one live connection and gives up.
_POOL_OPEN_TIMEOUT: float = 30.0

# Must stay identical to what peewee generates in cachetic/extensions/postgres.py.
# tests/test_postgres_cache.py compares both against information_schema.
_CREATE_TABLE_SQL = (
    'CREATE TABLE IF NOT EXISTS {} ("name" TEXT NOT NULL PRIMARY KEY, '
    '"value" TEXT NOT NULL, "expires_at" BIGINT)'
)


async def _close(pool: psycopg_pool.AsyncConnectionPool) -> None:
    await pool.close()


class AsyncPostgresCache(AsyncCacheProtocol):
    """PostgreSQL cache backend using psycopg3's async connection pool.

    Connection URL: ``postgresql://user:pass@host:5432/db?table=name``
    """

    _entry: _registry.Entry

    def __init__(self, cache_url: str) -> None:
        parts: PostgresUrlParts = parse_postgres_url(cache_url)
        safe_url: str = hide_url_password(cache_url)

        logger.debug("Initializing AsyncPostgresCache with URL: %s", safe_url)

        self._table = parts.table
        self._table_ident = sql.Identifier(parts.table)
        self._entry = _registry.acquire(
            _NAMESPACE,
            parts.db_url,
            # The pool is created closed: opening it is awaitable and happens in
            # _ensure_table(), under the entry's asyncio lock.
            factory=lambda: psycopg_pool.AsyncConnectionPool(parts.db_url, open=False),
            close=_close,
        )

    @property
    def _pool(self) -> psycopg_pool.AsyncConnectionPool:
        return self._entry.client

    async def _ensure_table(self) -> None:
        """Opens the pool and creates the table, once per client and table."""
        if self._table in self._entry.ensured:
            return

        async with self._entry.lock:
            if self._table in self._entry.ensured:
                return
            if _POOL_OPENED not in self._entry.ensured:
                # Opening is awaitable, which is why the pool is created closed.
                await self._pool.open(wait=True, timeout=_POOL_OPEN_TIMEOUT)
                self._entry.ensured.add(_POOL_OPENED)
            async with self._pool.connection() as conn:
                await conn.execute(sql.SQL(_CREATE_TABLE_SQL).format(self._table_ident))
            self._entry.ensured.add(self._table)
            logger.debug("Ensured table '%s' exists", self._table)

    async def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        """Stores a value with optional TTL (seconds from now).

        Deadline granularity matches the sync backend exactly, including its
        up-to-one-second lateness. See ``CacheticBase._ttl_to_expiry``.
        """
        await self._ensure_table()

        expires_at: int | None = None
        if ex is not None and ex > 0:
            expires_at = int(time.time()) + math.ceil(ex)

        query = sql.SQL(
            "INSERT INTO {} (name, value, expires_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (name) DO UPDATE "
            "SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at"
        ).format(self._table_ident)

        async with self._pool.connection() as conn:
            await conn.execute(query, (key, value.decode("utf-8"), expires_at))

    async def _fetch(self, key: str) -> tuple[str, int | None] | None:
        """Returns (value, expires_at) for ``key``, deleting it if expired."""
        select = sql.SQL("SELECT value, expires_at FROM {} WHERE name = %s").format(
            self._table_ident
        )

        async with self._pool.connection() as conn:
            cursor: psycopg.AsyncCursor = await conn.execute(select, (key,))
            row: tuple[typing.Any, ...] | None = await cursor.fetchone()

            if row is None:
                return None

            value, expires_at = row
            if expires_at is not None and expires_at < int(time.time()):
                delete = sql.SQL("DELETE FROM {} WHERE name = %s").format(
                    self._table_ident
                )
                await conn.execute(delete, (key,))
                return None

            return value, expires_at

    async def get(self, key: str, /) -> bytes | None:
        """Retrieves a value, returning None if missing or expired."""
        await self._ensure_table()

        row = await self._fetch(key)
        if row is None:
            return None
        return row[0].encode("utf-8")

    async def delete(self, key: str, /) -> None:
        """Removes a key from the cache."""
        await self._ensure_table()

        query = sql.SQL("DELETE FROM {} WHERE name = %s").format(self._table_ident)
        async with self._pool.connection() as conn:
            await conn.execute(query, (key,))

    async def exists(self, key: str, /) -> bool:
        """Checks existence, auto-cleaning expired entries."""
        await self._ensure_table()

        return await self._fetch(key) is not None
