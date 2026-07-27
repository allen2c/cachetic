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

    _entry: _registry.Entry

    def __init__(self, cache_url: str) -> None:
        """Initializes the cache from a MongoDB URL.

        The URL must contain a database path and a collection query parameter.
        """
        parts: MongoUrlParts = parse_mongo_url(cache_url)
        safe_url: str = hide_url_password(parts.db_url)

        logger.debug(f"Initializing AsyncMongoCache with URL: {safe_url}")

        self._database = parts.database
        self._collection = parts.collection
        self._entry = _registry.acquire(
            _NAMESPACE,
            parts.db_url,
            factory=lambda: pymongo.AsyncMongoClient(
                parts.db_url, document_class=DocumentParam
            ),
            close=_close,
        )

    @property
    def client(self) -> pymongo.AsyncMongoClient:
        return self._entry.client

    @property
    def col(self):
        return self.client[self._database][self._collection]

    async def _ensure_index(self) -> None:
        """Creates the unique index on ``name``, once per client and collection."""
        index_key = (self._database, self._collection)
        if index_key in self._entry.ensured:
            return

        async with self._entry.lock:
            if index_key in self._entry.ensured:
                return
            await self.col.create_index("name", unique=True)
            self._entry.ensured.add(index_key)
            logger.debug(
                f"Ensured unique index on 'name' in collection: {self._collection}"
            )

    async def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        """Sets a key-value pair, with an optional expiration in seconds.

        Deadline granularity matches the sync backend exactly, including its
        up-to-one-second lateness. See ``CacheticBase._ttl_to_expiry``.
        """
        await self._ensure_index()

        expires_at = None if ex is None or ex < 1 else int(time.time()) + math.ceil(ex)

        logger.debug(
            f"[AsyncMongoCache.set] Setting key='{key}', "
            f"value_size={len(value) if hasattr(value, '__len__') else 'unknown'}, "
            f"ex={expires_at}"
        )

        await self.col.update_one(
            {"name": key}, {"$set": {"value": value, "ex": expires_at}}, upsert=True
        )

    async def get(self, key: str, /) -> bytes | None:
        """Retrieves a value by key.

        Returns None if the key doesn't exist or has expired.
        """
        await self._ensure_index()

        doc: DocumentParam | None = await self.col.find_one({"name": key})
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
            await self.col.delete_one({"name": key})
            return None

        return doc["value"]

    async def delete(self, key: str, /) -> None:
        """Deletes a key-value pair from the cache."""
        await self._ensure_index()
        await self.col.delete_one({"name": key})

    async def exists(self, key: str, /) -> bool:
        """Checks if a key exists and has not expired."""
        await self._ensure_index()

        doc: DocumentParam | None = await self.col.find_one({"name": key})
        if doc is None:
            return False

        expires_at: int | None = doc["ex"]
        if expires_at is not None and expires_at < int(time.time()):
            await self.col.delete_one({"name": key})
            return False
        return True
