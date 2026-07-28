"""Async Redis adapter for AsyncCacheProtocol.

Wraps ``redis.asyncio.Redis``. Clients are shared per (event loop, URL) via the
async registry, mirroring how the sync adapter shares clients per URL.
"""

import logging
import typing

import redis.asyncio

from cachetic.extensions import _ttl
from cachetic.extensions.aio import _registry
from cachetic.types.async_cache_protocol import AsyncCacheProtocol

logger = logging.getLogger(__name__)

_NAMESPACE = "redis"


class AsyncRedisCacheAdapter(AsyncCacheProtocol):
    """Adapts ``redis.asyncio.Redis`` to the ``AsyncCacheProtocol`` interface."""

    _entry: _registry.EntryHandle | None

    def __init__(self, url: str | None = None, *, client: "redis.asyncio.Redis | None" = None) -> None:  # type: ignore[type-arg]
        """Wraps a shared client for ``url``, or a ``redis.asyncio.Redis`` given to it.

        A supplied client is used as-is and never registered, for the same
        reason as the sync adapter — with one extra: an async client belongs to
        the loop that built it, and the caller owns that too.
        """
        if client is not None:
            self._entry = None
            self._supplied = client
            return
        if url is None:
            raise ValueError("AsyncRedisCacheAdapter needs either a url or a client")

        self._entry = _registry.EntryHandle(
            _NAMESPACE,
            url,
            factory=lambda: redis.asyncio.Redis.from_url(url),
            close=_close,
        )

    @property
    def _client(self) -> "redis.asyncio.Redis":  # type: ignore[type-arg]
        if self._entry is None:
            return self._supplied
        return self._entry().client

    async def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        await self._client.set(key, value, ex=_ttl.expiry_seconds(ex))

    async def get(self, key: str, /) -> bytes | None:
        return typing.cast(bytes | None, await self._client.get(key))

    async def delete(self, key: str, /) -> None:
        await self._client.delete(key)

    async def exists(self, key: str, /) -> bool:
        return await self._client.exists(key) > 0


async def _close(client: redis.asyncio.Redis) -> None:  # type: ignore[type-arg]
    await client.aclose()
