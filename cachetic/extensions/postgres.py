"""Postgres adapter for CacheProtocol.

Talks to psycopg3 through a :class:`psycopg_pool.ConnectionPool`, mirroring the
async backend in :mod:`cachetic.extensions.aio.postgres`. Both compose their
statements from :mod:`cachetic.extensions._postgres_sql`, so the schema and the
queries are shared rather than duplicated.

The connection URL is handed to psycopg verbatim as a libpq conninfo string, so
options such as ``sslmode`` or ``application_name`` are honoured identically by
both backends. Pool size comes from ``?pool_min_size=`` / ``?pool_max_size=``.

Pools are shared per URL through :mod:`cachetic.extensions._registry`, and
released by :func:`cachetic.close_all`.

``value`` is stored as ``TEXT``. That is safe because every value written
through ``Cachetic`` is a base64 Data URL, hence ASCII.
"""

import logging
import time
import typing

import psycopg
import psycopg_pool
from psycopg import sql

from cachetic.extensions import _postgres_sql, _registry, _ttl
from cachetic.extensions._url import PostgresUrlParts, parse_postgres_url
from cachetic.types.cache_protocol import CacheProtocol
from cachetic.utils.hide_url_password import hide_url_password

logger = logging.getLogger(__name__)

_NAMESPACE = "postgres"

# Marks a pool as opened inside Entry.ensured, alongside the table names.
_POOL_OPENED = "\0pool-opened"

_Pool = psycopg_pool.ConnectionPool[typing.Any]

_CONCURRENT_CREATE_ERRORS = (psycopg.errors.DuplicateTable, psycopg.errors.UniqueViolation)
"""What ``CREATE TABLE IF NOT EXISTS`` raises when it loses a race.

The entry lock serialises table creation *within* a process, but the registry is
process-local and ``IF NOT EXISTS`` is not atomic across sessions: the existence
check and the catalog insert are separate steps, so a fleet starting at once
against an empty database can have several processes pass the check and one of
them fail on the insert. The table exists either way, which is all this needed.
"""


class PostgresCache(CacheProtocol):
    """PostgreSQL cache backend using psycopg3's connection pool.

    Connection URL: ``postgresql://user:pass@host:5432/db?table=name``
    """

    _entry: _registry.EntryHandle
    _table: str

    def __init__(self, cache_url: str) -> None:
        parts: PostgresUrlParts = parse_postgres_url(cache_url)
        safe_url: str = hide_url_password(cache_url)

        logger.debug("Initializing PostgresCache with URL: %s", safe_url)

        self._table = parts.table
        self._table_ident = sql.Identifier(parts.table)
        self._entry = _registry.EntryHandle(
            _NAMESPACE,
            parts.db_url,
            # Created closed so that opening — which blocks on a live connection
            # — happens under the entry's lock in _ready_pool().
            factory=lambda: psycopg_pool.ConnectionPool(
                parts.db_url,
                min_size=parts.pool_min_size,
                max_size=parts.pool_max_size,
                open=False,
            ),
            close=_close,
        )

    def _ready_pool(self) -> _Pool:
        """Returns an open pool whose table exists, doing the setup once."""
        entry = self._entry()
        pool: _Pool = entry.client

        if self._table in entry.ensured:
            return pool

        with entry.lock:
            if self._table in entry.ensured:
                return pool
            if _POOL_OPENED not in entry.ensured:
                pool.open(wait=True, timeout=_postgres_sql.DEFAULT_POOL_OPEN_TIMEOUT)
                entry.ensured.add(_POOL_OPENED)
            try:
                with pool.connection() as conn:
                    conn.execute(_postgres_sql.CREATE_TABLE.format(self._table_ident))
            except _CONCURRENT_CREATE_ERRORS:
                # Caught outside the connection block so its rollback runs first.
                logger.debug("Table '%s' was created concurrently; treating as ensured", self._table)
            entry.ensured.add(self._table)
            logger.debug("Ensured table '%s' exists", self._table)

        return pool

    def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        """Stores a value with optional TTL (seconds from now).

        The deadline is whole-second and derived from a truncated clock, so an
        entry may outlive its TTL by up to a second. See
        ``CacheticBase._ttl_to_expiry`` for why that is accepted.
        """
        pool = self._ready_pool()

        expires_at: int | None = _ttl.deadline(ex)

        query = _postgres_sql.UPSERT.format(self._table_ident)
        with pool.connection() as conn:
            conn.execute(query, (key, value.decode("utf-8"), expires_at))

    def _fetch(self, key: str) -> tuple[str, int | None] | None:
        """Returns (value, expires_at) for ``key``, deleting it if expired."""
        pool = self._ready_pool()
        select = _postgres_sql.SELECT.format(self._table_ident)

        with pool.connection() as conn:
            row: tuple[typing.Any, ...] | None = conn.execute(select, (key,)).fetchone()

            if row is None:
                return None

            value, expires_at = row
            if expires_at is not None and expires_at < int(time.time()):
                # Conditional on the deadline just read, so a concurrent set()
                # that replaced the entry is not clobbered.
                delete = _postgres_sql.DELETE_EXPIRED.format(self._table_ident)
                conn.execute(delete, (key, expires_at))
                return None

            return value, expires_at

    def get(self, key: str, /) -> bytes | None:
        """Retrieves a value, returning None if missing or expired."""
        row = self._fetch(key)
        if row is None:
            return None
        return row[0].encode("utf-8")

    def delete(self, key: str, /) -> None:
        """Removes a key from the cache."""
        pool = self._ready_pool()

        query = _postgres_sql.DELETE.format(self._table_ident)
        with pool.connection() as conn:
            conn.execute(query, (key,))

    def exists(self, key: str, /) -> bool:
        """Checks existence, auto-cleaning expired entries."""
        return self._fetch(key) is not None


def _close(pool: _Pool) -> None:
    pool.close()
