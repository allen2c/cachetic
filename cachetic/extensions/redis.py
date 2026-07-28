"""Redis adapter for CacheProtocol.

Wraps ``redis.Redis`` to provide a unified cache interface. Clients are shared
per URL through :mod:`cachetic.extensions._registry`, and released by
:func:`cachetic.close_all`.
"""

import logging
import typing

import redis

from cachetic.extensions import _registry, _ttl
from cachetic.types.cache_protocol import CacheProtocol

logger = logging.getLogger(__name__)

_NAMESPACE = "redis"


class RedisCacheAdapter(CacheProtocol):
    """Adapts ``redis.Redis`` to the ``CacheProtocol`` interface.

    Clients are shared per URL to avoid redundant connection pools.
    """

    _entry: _registry.EntryHandle | None

    def __init__(self, url: str | None = None, *, client: "redis.Redis | None" = None) -> None:  # type: ignore[type-arg]
        """Wraps a shared client for ``url``, or a ``redis.Redis`` given to it.

        A supplied client is used as-is and never registered: the registry keys
        on a URL, this has none, and :func:`cachetic.close_all` must not close a
        connection pool it did not open. See
        ``CacheticBase.accept_a_live_backend_client``.
        """
        if client is not None:
            self._entry = None
            self._supplied = client
            return
        if url is None:
            raise ValueError("RedisCacheAdapter needs either a url or a client")

        self._entry = _registry.EntryHandle(
            _NAMESPACE,
            url,
            factory=lambda: redis.Redis.from_url(url),
            close=_close,
        )

    @property
    def _client(self) -> "redis.Redis":  # type: ignore[type-arg]
        if self._entry is None:
            return self._supplied
        return self._entry().client

    def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        self._client.set(key, value, ex=_ttl.expiry_seconds(ex))

    def get(self, key: str, /) -> bytes | None:
        return typing.cast(bytes | None, self._client.get(key))

    def delete(self, key: str, /) -> None:
        self._client.delete(key)

    def exists(self, key: str, /) -> bool:
        return self._client.exists(key) > 0


def _close(client: "redis.Redis") -> None:  # type: ignore[type-arg]
    client.close()
