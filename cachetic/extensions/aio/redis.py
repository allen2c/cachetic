"""Async Redis adapter for AsyncCacheProtocol.

Wraps ``redis.asyncio.Redis``. Clients are shared per (event loop, URL) via the
async registry, mirroring how the sync adapter shares clients per URL.
"""

import logging
import typing

import redis.asyncio

from cachetic.extensions.aio import _registry
from cachetic.types.async_cache_protocol import AsyncCacheProtocol

logger = logging.getLogger(__name__)

_NAMESPACE = "redis"


async def _close(client: redis.asyncio.Redis) -> None:  # type: ignore[type-arg]
    await client.aclose()


class AsyncRedisCacheAdapter(AsyncCacheProtocol):
    """Adapts ``redis.asyncio.Redis`` to the ``AsyncCacheProtocol`` interface."""

    _entry: _registry.EntryHandle

    def __init__(self, url: str) -> None:
        self._entry = _registry.EntryHandle(
            _NAMESPACE,
            url,
            factory=lambda: redis.asyncio.Redis.from_url(url),
            close=_close,
        )

    @property
    def _client(self) -> "redis.asyncio.Redis":  # type: ignore[type-arg]
        return self._entry().client

    async def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        await self._client.set(key, value, ex=ex)

    async def get(self, key: str, /) -> bytes | None:
        return typing.cast(bytes | None, await self._client.get(key))

    async def delete(self, key: str, /) -> None:
        await self._client.delete(key)

    async def exists(self, key: str, /) -> bool:
        return await self._client.exists(key) > 0
