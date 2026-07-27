"""MongoDB adapter for CacheProtocol.

Reuses MongoClient instances per connection URL and ensures the unique index on
``name`` only once per (database, collection) pair.
"""

import logging
import math
import time

import pydantic
import pymongo

from cachetic.extensions._url import MongoUrlParts, parse_mongo_url
from cachetic.types.cache_protocol import CacheProtocol
from cachetic.types.document_param import DocumentParam
from cachetic.utils.hide_url_password import hide_url_password

logger = logging.getLogger(__name__)

# CAC-001: Module-level registry to share MongoClient instances per connection URL
_client_registry: dict[str, pymongo.MongoClient] = {}

# CAC-002: Track (db, collection) pairs that already had their indexes ensured
_ensured_indexes: set[tuple[str, str]] = set()


class MongoCache(CacheProtocol):
    """A cache that uses MongoDB as a backend."""

    def __init__(self, cache_url: str):
        """Initializes the cache from a MongoDB URL.

        The URL must contain a database path and a collection query parameter.
        """
        parts: MongoUrlParts = parse_mongo_url(cache_url)
        safe_url: str = hide_url_password(parts.db_url)

        logger.debug(f"Initializing MongoCache with URL: {safe_url}")

        # CAC-001: Reuse MongoClient from registry if available
        if parts.db_url in _client_registry:
            mongo_client = _client_registry[parts.db_url]
            logger.debug(f"Reusing existing MongoClient for: {safe_url}")
        else:
            mongo_client = pymongo.MongoClient(
                parts.db_url, document_class=DocumentParam
            )
            _client_registry[parts.db_url] = mongo_client
            logger.debug(f"Created new MongoClient for: {safe_url}")

        db = mongo_client[parts.database]
        col = db[parts.collection]

        # CAC-002: Only ensure index once per (db, collection) pair
        index_key = (parts.database, parts.collection)
        if index_key not in _ensured_indexes:
            col.create_index("name", unique=True)
            _ensured_indexes.add(index_key)
            logger.debug(
                f"Ensured unique index on 'name' in collection: {parts.collection}"
            )

        self.cache_url = pydantic.SecretStr(parts.db_url)
        self.client = mongo_client
        self.db = db
        self.col = col

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

        self.col.update_one(
            {"name": key}, {"$set": {"value": value, "ex": expires_at}}, upsert=True
        )

    def get(self, key: str, /) -> bytes | None:
        """Retrieves a value by key.

        Returns None if the key doesn't exist or has expired.
        """
        logger.debug(f"[MongoCache.get] Getting key='{key}'")
        doc: DocumentParam | None = self.col.find_one({"name": key})

        if doc is None:
            logger.debug(f"[MongoCache.get] Key='{key}' not found.")
            return None

        expires_at: int | None = doc["ex"]
        if expires_at is None:
            logger.debug(f"[MongoCache.get] Key='{key}' found (no expiration).")
            return doc["value"]

        if expires_at < int(time.time()):
            logger.debug(
                f"[MongoCache.get] Key='{key}' expired at {expires_at}, "
                f"now={int(time.time())}. Deleting."
            )
            self.col.delete_one({"name": key})
            return None

        logger.debug(f"[MongoCache.get] Key='{key}' found and valid.")
        return doc["value"]

    def delete(self, key: str, /) -> None:
        """Deletes a key-value pair from the cache."""
        logger.debug(f"[MongoCache.delete] Deleting key='{key}'")
        self.col.delete_one({"name": key})

    def exists(self, key: str, /) -> bool:
        """Checks if a key exists and has not expired."""
        doc: DocumentParam | None = self.col.find_one({"name": key})
        if doc is None:
            return False
        expires_at: int | None = doc["ex"]
        if expires_at is not None and expires_at < int(time.time()):
            self.col.delete_one({"name": key})
            return False
        return True
