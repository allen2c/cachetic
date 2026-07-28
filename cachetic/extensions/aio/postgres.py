"""Async PostgreSQL adapter for AsyncCacheProtocol.

Talks to psycopg3 through an ``AsyncConnectionPool``. The schema and every
statement come from :mod:`cachetic.extensions._postgres_sql`, shared with the
sync backend, so the two cannot drift apart.

Pool size comes from the URL's ``?pool_min_size=`` / ``?pool_max_size=``, the
same parameters the sync backend reads.

``value`` is stored as ``TEXT``. That is safe because every value written
through ``Cachetic`` is a base64 Data URL, hence ASCII.
"""

import logging
import math
import time
import typing

import psycopg
import psycopg_pool
from psycopg import sql

from cachetic.extensions import _postgres_sql
from cachetic.extensions._url import PostgresUrlParts, parse_postgres_url
from cachetic.extensions.aio import _registry
from cachetic.types.async_cache_protocol import AsyncCacheProtocol
from cachetic.utils.hide_url_password import hide_url_password

logger = logging.getLogger(__name__)

_NAMESPACE = "postgres"

# Marks a pool as opened inside Entry.ensured, alongside the table names.
_POOL_OPENED = "\0pool-opened"

_Pool = psycopg_pool.AsyncConnectionPool[typing.Any]


async def _close(pool: _Pool) -> None:
    await pool.close()


class AsyncPostgresCache(AsyncCacheProtocol):
    """PostgreSQL cache backend using psycopg3's async connection pool.

    Connection URL: ``postgresql://user:pass@host:5432/db?table=name``
    """

    def __init__(self, cache_url: str) -> None:
        parts: PostgresUrlParts = parse_postgres_url(cache_url)
        safe_url: str = hide_url_password(cache_url)

        logger.debug("Initializing AsyncPostgresCache with URL: %s", safe_url)

        self._table = parts.table
        self._table_ident = sql.Identifier(parts.table)
        self._entry = _registry.EntryHandle(
            _NAMESPACE,
            parts.db_url,
            # The pool is created closed: opening it is awaitable and happens in
            # _ready_pool(), under the entry's asyncio lock.
            factory=lambda: psycopg_pool.AsyncConnectionPool(
                parts.db_url,
                min_size=parts.pool_min_size,
                max_size=parts.pool_max_size,
                open=False,
            ),
            close=_close,
        )

    async def _ready_pool(self) -> _Pool:
        """Returns an open pool whose table exists, doing the setup once."""
        entry = self._entry()
        pool: _Pool = entry.client

        if self._table in entry.ensured:
            return pool

        async with entry.lock:
            if self._table in entry.ensured:
                return pool
            if _POOL_OPENED not in entry.ensured:
                # Opening is awaitable, which is why the pool is created closed.
                await pool.open(
                    wait=True, timeout=_postgres_sql.DEFAULT_POOL_OPEN_TIMEOUT
                )
                entry.ensured.add(_POOL_OPENED)
            async with pool.connection() as conn:
                await conn.execute(_postgres_sql.CREATE_TABLE.format(self._table_ident))
            entry.ensured.add(self._table)
            logger.debug("Ensured table '%s' exists", self._table)

        return pool

    async def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        """Stores a value with optional TTL (seconds from now).

        Deadline granularity matches the sync backend exactly, including its
        up-to-one-second lateness. See ``CacheticBase._ttl_to_expiry``.
        """
        pool = await self._ready_pool()

        expires_at: int | None = None
        if ex is not None and ex > 0:
            expires_at = int(time.time()) + math.ceil(ex)

        query = _postgres_sql.UPSERT.format(self._table_ident)
        async with pool.connection() as conn:
            await conn.execute(query, (key, value.decode("utf-8"), expires_at))

    async def _fetch(self, key: str) -> tuple[str, int | None] | None:
        """Returns (value, expires_at) for ``key``, deleting it if expired."""
        pool = await self._ready_pool()
        select = _postgres_sql.SELECT.format(self._table_ident)

        async with pool.connection() as conn:
            cursor: psycopg.AsyncCursor = await conn.execute(select, (key,))
            row: tuple[typing.Any, ...] | None = await cursor.fetchone()

            if row is None:
                return None

            value, expires_at = row
            if expires_at is not None and expires_at < int(time.time()):
                # Conditional on the deadline just read, so a concurrent set()
                # that replaced the entry is not clobbered.
                delete = _postgres_sql.DELETE_EXPIRED.format(self._table_ident)
                await conn.execute(delete, (key, expires_at))
                return None

            return value, expires_at

    async def get(self, key: str, /) -> bytes | None:
        """Retrieves a value, returning None if missing or expired."""
        row = await self._fetch(key)
        if row is None:
            return None
        return row[0].encode("utf-8")

    async def delete(self, key: str, /) -> None:
        """Removes a key from the cache."""
        pool = await self._ready_pool()

        query = _postgres_sql.DELETE.format(self._table_ident)
        async with pool.connection() as conn:
            await conn.execute(query, (key,))

    async def exists(self, key: str, /) -> bool:
        """Checks existence, auto-cleaning expired entries."""
        return await self._fetch(key) is not None
