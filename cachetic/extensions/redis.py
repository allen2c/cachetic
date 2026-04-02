"""Redis adapter for CacheProtocol.

Wraps ``redis.Redis`` to provide a unified cache interface.
Reuses Redis clients per URL via a module-level registry.
"""

import logging
import typing

import redis

from cachetic.types.cache_protocol import CacheProtocol

logger = logging.getLogger(__name__)

_client_registry: dict[str, redis.Redis] = {}  # type: ignore[type-arg]


class RedisCacheAdapter(CacheProtocol):
    """Adapts ``redis.Redis`` to the ``CacheProtocol`` interface.

    Clients are shared per URL to avoid redundant connection pools.
    """

    _client: redis.Redis  # type: ignore[type-arg]

    def __init__(self, url: str) -> None:
        if url in _client_registry:
            self._client = _client_registry[url]
            logger.debug("Reusing existing Redis client for: %s", url)
        else:
            self._client = redis.Redis.from_url(url)
            _client_registry[url] = self._client
            logger.debug("Created new Redis client for: %s", url)

    def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        self._client.set(key, value, ex=ex)

    def get(self, key: str, /) -> bytes | None:
        return typing.cast(bytes | None, self._client.get(key))

    def delete(self, key: str, /) -> None:
        self._client.delete(key)

    def exists(self, key: str, /) -> bool:
        return self._client.exists(key) > 0
