"""Postgres adapter for CacheProtocol.

Uses peewee ORM with psycopg (v3) driver.
Reuses database connections per URL via a module-level registry.
"""

import logging
import math
import time
import typing

import peewee

from cachetic.extensions._url import PostgresUrlParts, parse_postgres_url
from cachetic.types.cache_protocol import CacheProtocol
from cachetic.utils.hide_url_password import hide_url_password

logger = logging.getLogger(__name__)

_db_registry: dict[str, peewee.PostgresqlDatabase] = {}
_ensured_tables: set[tuple[str, str]] = set()


def _make_cache_model(db: peewee.PostgresqlDatabase, table: str) -> type[peewee.Model]:
    """Creates a peewee Model class bound to the given database and table."""

    class CacheEntry(peewee.Model):
        name = peewee.TextField(primary_key=True)
        value = peewee.TextField()
        expires_at = peewee.BigIntegerField(null=True)

        class Meta:
            database = db
            table_name = table

    return CacheEntry


class PostgresCache(CacheProtocol):
    """PostgreSQL cache backend using peewee ORM with psycopg3.

    Connection URL: ``postgresql://user:pass@host:5432/db?table=name``
    """

    _db: peewee.PostgresqlDatabase
    _model: type[peewee.Model]

    def __init__(self, cache_url: str) -> None:
        parts: PostgresUrlParts = parse_postgres_url(cache_url)
        safe_url: str = hide_url_password(cache_url)
        db_url: str = parts.db_url
        table_name: str = parts.table

        if db_url in _db_registry:
            db = _db_registry[db_url]
            logger.debug("Reusing existing Postgres connection for: %s", safe_url)
        else:
            db = peewee.PostgresqlDatabase(
                parts.database,
                host=parts.host,
                port=parts.port,
                user=parts.user,
                password=parts.password,
            )
            _db_registry[db_url] = db
            logger.debug("Created new Postgres connection for: %s", safe_url)

        model: type[peewee.Model] = _make_cache_model(db, table_name)

        table_key: tuple[str, str] = (db_url, table_name)
        if table_key not in _ensured_tables:
            db.create_tables([model])
            _ensured_tables.add(table_key)
            logger.debug("Ensured table '%s' exists", table_name)

        self._db = db
        self._model = model

    def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        """Stores a value with optional TTL (seconds from now).

        The deadline is whole-second and derived from a truncated clock, so an
        entry may outlive its TTL by up to a second. See
        ``CacheticBase._ttl_to_expiry`` for why that is accepted.
        """
        expires_at: int | None = None
        if ex is not None and ex > 0:
            expires_at = int(time.time()) + math.ceil(ex)

        value_text: str = value.decode("utf-8")
        (
            self._model.insert(name=key, value=value_text, expires_at=expires_at)
            .on_conflict(
                conflict_target=[self._model.name],
                update={
                    self._model.value: value_text,
                    self._model.expires_at: expires_at,
                },
            )
            .execute()
        )

    def get(self, key: str, /) -> bytes | None:
        """Retrieves a value, returning None if missing or expired."""
        try:
            entry: peewee.Model = self._model.get(self._model.name == key)
        except self._model.DoesNotExist:
            return None

        if entry.expires_at is not None and entry.expires_at < int(time.time()):
            self._model.delete().where(self._model.name == key).execute()
            return None

        return typing.cast(str, entry.value).encode("utf-8")

    def delete(self, key: str, /) -> None:
        """Removes a key from the cache."""
        self._model.delete().where(self._model.name == key).execute()

    def exists(self, key: str, /) -> bool:
        """Checks existence, auto-cleaning expired entries."""
        try:
            entry: peewee.Model = self._model.get(self._model.name == key)
        except self._model.DoesNotExist:
            return False

        if entry.expires_at is not None and entry.expires_at < int(time.time()):
            self._model.delete().where(self._model.name == key).execute()
            return False
        return True
