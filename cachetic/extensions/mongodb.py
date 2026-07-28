"""MongoDB adapter for CacheProtocol.

Clients are shared per connection URL through
:mod:`cachetic.extensions._registry`, and released by
:func:`cachetic.close_all`. The unique index on ``name`` is created once per
client and per (database, collection) pair.
"""

import logging
import math
import time

import pydantic
import pymongo

from cachetic.extensions import _registry
from cachetic.extensions._url import MongoUrlParts, parse_mongo_url
from cachetic.types.cache_protocol import CacheProtocol
from cachetic.types.document_param import DocumentParam
from cachetic.utils.hide_url_password import hide_url_password

logger = logging.getLogger(__name__)

_NAMESPACE = "mongodb"


class MongoCache(CacheProtocol):
    """A cache that uses MongoDB as a backend."""

    _entry: _registry.EntryHandle

    def __init__(self, cache_url: str):
        """Initializes the cache from a MongoDB URL.

        The URL must contain a database path and a collection query parameter.
        """
        parts: MongoUrlParts = parse_mongo_url(cache_url)
        safe_url: str = hide_url_password(parts.db_url)

        logger.debug(f"Initializing MongoCache with URL: {safe_url}")

        self._database = parts.database
        self._collection = parts.collection
        self.cache_url = pydantic.SecretStr(parts.db_url)
        self._entry = _registry.EntryHandle(
            _NAMESPACE,
            parts.db_url,
            factory=lambda: pymongo.MongoClient(parts.db_url, document_class=DocumentParam),
            close=_close,
        )

    @property
    def client(self) -> pymongo.MongoClient:  # type: ignore[type-arg]
        return self._entry().client

    @property
    def col(self):
        """The collection, with its unique index created once per client."""
        entry = self._entry()
        col = entry.client[self._database][self._collection]

        index_key = (self._database, self._collection)
        if index_key in entry.ensured:
            return col

        with entry.lock:
            if index_key not in entry.ensured:
                col.create_index("name", unique=True)
                entry.ensured.add(index_key)
                logger.debug(f"Ensured unique index on 'name' in collection: {self._collection}")
        return col

    def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        """Sets a key-value pair, with an optional expiration in seconds.

        The deadline is whole-second and derived from a truncated clock, so an
        entry may outlive its TTL by up to a second. See
        ``CacheticBase._ttl_to_expiry`` for why that is accepted.
        """
        expires_at = None if ex is None or ex < 1 else int(time.time()) + math.ceil(ex)

        logger.debug(
            f"[MongoCache.set] Setting key='{key}', "
            f"value_size={len(value) if hasattr(value, '__len__') else 'unknown'}, "
            f"ex={expires_at}"
        )

        self.col.update_one({"name": key}, {"$set": {"value": value, "ex": expires_at}}, upsert=True)

    def get(self, key: str, /) -> bytes | None:
        """Retrieves a value by key.

        Returns None if the key doesn't exist or has expired.
        """
        logger.debug(f"[MongoCache.get] Getting key='{key}'")
        col = self.col
        doc: DocumentParam | None = col.find_one({"name": key})

        if doc is None:
            logger.debug(f"[MongoCache.get] Key='{key}' not found.")
            return None

        expires_at: int | None = doc["ex"]
        if expires_at is None:
            logger.debug(f"[MongoCache.get] Key='{key}' found (no expiration).")
            return doc["value"]

        if expires_at < int(time.time()):
            logger.debug(f"[MongoCache.get] Key='{key}' expired at {expires_at}, now={int(time.time())}. Deleting.")
            self._delete_if_expired(col, key, expires_at)
            return None

        logger.debug(f"[MongoCache.get] Key='{key}' found and valid.")
        return doc["value"]

    @staticmethod
    def _delete_if_expired(col, key: str, expires_at: int) -> None:
        """Removes an expired entry, unless it has since been rewritten.

        The filter pins ``ex`` to the deadline that was just read: a concurrent
        ``set`` between that read and this delete replaces the entry, and its
        value must not be dropped by this cleanup.
        """
        col.delete_one({"name": key, "ex": expires_at})

    def delete(self, key: str, /) -> None:
        """Deletes a key-value pair from the cache."""
        logger.debug(f"[MongoCache.delete] Deleting key='{key}'")
        self.col.delete_one({"name": key})

    def exists(self, key: str, /) -> bool:
        """Checks if a key exists and has not expired."""
        col = self.col
        doc: DocumentParam | None = col.find_one({"name": key})
        if doc is None:
            return False
        expires_at: int | None = doc["ex"]
        if expires_at is not None and expires_at < int(time.time()):
            self._delete_if_expired(col, key, expires_at)
            return False
        return True


def _close(client: pymongo.MongoClient) -> None:  # type: ignore[type-arg]
    client.close()
