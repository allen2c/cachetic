"""Async MongoDB adapter for AsyncCacheProtocol.

Uses ``pymongo.AsyncMongoClient`` (pymongo >= 4.9). Clients are shared per
(event loop, URL); the unique index on ``name`` is created once per client and
per (database, collection) pair, guarded by that entry's asyncio lock.

The "already ensured" bookkeeping deliberately lives on the registry entry
rather than in a module-level set: a fresh event loop gets a fresh client, which
must create its index again.
"""

import logging
import math
import time

import pymongo

from cachetic.extensions._url import MongoUrlParts, parse_mongo_url
from cachetic.extensions.aio import _registry
from cachetic.types.async_cache_protocol import AsyncCacheProtocol
from cachetic.types.document_param import DocumentParam
from cachetic.utils.hide_url_password import hide_url_password

logger = logging.getLogger(__name__)

_NAMESPACE = "mongodb"


async def _close(client: pymongo.AsyncMongoClient) -> None:
    await client.close()


class AsyncMongoCache(AsyncCacheProtocol):
    """A cache that uses MongoDB as a backend, over pymongo's async client."""

    _entry: _registry.EntryHandle

    def __init__(self, cache_url: str) -> None:
        """Initializes the cache from a MongoDB URL.

        The URL must contain a database path and a collection query parameter.
        """
        parts: MongoUrlParts = parse_mongo_url(cache_url)
        safe_url: str = hide_url_password(parts.db_url)

        logger.debug(f"Initializing AsyncMongoCache with URL: {safe_url}")

        self._database = parts.database
        self._collection = parts.collection
        self._entry = _registry.EntryHandle(
            _NAMESPACE,
            parts.db_url,
            factory=lambda: pymongo.AsyncMongoClient(
                parts.db_url, document_class=DocumentParam
            ),
            close=_close,
        )

    async def _ready_col(self):
        """Returns the collection, creating its unique index once per client."""
        entry = self._entry()
        col = entry.client[self._database][self._collection]

        index_key = (self._database, self._collection)
        if index_key in entry.ensured:
            return col

        async with entry.lock:
            if index_key in entry.ensured:
                return col
            await col.create_index("name", unique=True)
            entry.ensured.add(index_key)
            logger.debug(
                f"Ensured unique index on 'name' in collection: {self._collection}"
            )
        return col

    async def _delete_if_expired(self, col, key: str, expires_at: int) -> None:
        """Removes an expired entry, unless it has since been rewritten.

        The filter pins ``ex`` to the deadline that was just read: a concurrent
        ``set`` between that read and this delete replaces the entry, and its
        value must not be dropped by this cleanup.
        """
        await col.delete_one({"name": key, "ex": expires_at})

    async def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        """Sets a key-value pair, with an optional expiration in seconds.

        Deadline granularity matches the sync backend exactly, including its
        up-to-one-second lateness. See ``CacheticBase._ttl_to_expiry``.
        """
        col = await self._ready_col()

        expires_at = None if ex is None or ex < 1 else int(time.time()) + math.ceil(ex)

        logger.debug(
            f"[AsyncMongoCache.set] Setting key='{key}', "
            f"value_size={len(value) if hasattr(value, '__len__') else 'unknown'}, "
            f"ex={expires_at}"
        )

        await col.update_one(
            {"name": key}, {"$set": {"value": value, "ex": expires_at}}, upsert=True
        )

    async def get(self, key: str, /) -> bytes | None:
        """Retrieves a value by key.

        Returns None if the key doesn't exist or has expired.
        """
        col = await self._ready_col()

        doc: DocumentParam | None = await col.find_one({"name": key})
        if doc is None:
            logger.debug(f"[AsyncMongoCache.get] Key='{key}' not found.")
            return None

        expires_at: int | None = doc["ex"]
        if expires_at is None:
            return doc["value"]

        if expires_at < int(time.time()):
            logger.debug(
                f"[AsyncMongoCache.get] Key='{key}' expired at {expires_at}. Deleting."
            )
            await self._delete_if_expired(col, key, expires_at)
            return None

        return doc["value"]

    async def delete(self, key: str, /) -> None:
        """Deletes a key-value pair from the cache."""
        col = await self._ready_col()
        await col.delete_one({"name": key})

    async def exists(self, key: str, /) -> bool:
        """Checks if a key exists and has not expired."""
        col = await self._ready_col()

        doc: DocumentParam | None = await col.find_one({"name": key})
        if doc is None:
            return False

        expires_at: int | None = doc["ex"]
        if expires_at is not None and expires_at < int(time.time()):
            await self._delete_if_expired(col, key, expires_at)
            return False
        return True
