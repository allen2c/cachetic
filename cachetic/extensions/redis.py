"""Redis adapter for CacheProtocol.

Wraps ``redis.Redis`` to provide a unified cache interface. Clients are shared
per URL through :mod:`cachetic.extensions._registry`, and released by
:func:`cachetic.close_all`.
"""

import logging
import typing

import redis

from cachetic.extensions import _registry
from cachetic.types.cache_protocol import CacheProtocol

logger = logging.getLogger(__name__)

_NAMESPACE = "redis"


class RedisCacheAdapter(CacheProtocol):
    """Adapts ``redis.Redis`` to the ``CacheProtocol`` interface.

    Clients are shared per URL to avoid redundant connection pools.
    """

    _entry: _registry.EntryHandle

    def __init__(self, url: str) -> None:
        self._entry = _registry.EntryHandle(
            _NAMESPACE,
            url,
            factory=lambda: redis.Redis.from_url(url),
            close=_close,
        )

    @property
    def _client(self) -> "redis.Redis":  # type: ignore[type-arg]
        return self._entry().client

    def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        self._client.set(key, value, ex=ex)

    def get(self, key: str, /) -> bytes | None:
        return typing.cast(bytes | None, self._client.get(key))

    def delete(self, key: str, /) -> None:
        self._client.delete(key)

    def exists(self, key: str, /) -> bool:
        return self._client.exists(key) > 0


def _close(client: "redis.Redis") -> None:  # type: ignore[type-arg]
    client.close()
