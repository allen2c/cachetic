"""Async DiskCache adapter for AsyncCacheProtocol.

``diskcache`` has no async API, so each operation is offloaded to a worker
thread. This is thread offload, not true async I/O: it keeps the event loop
responsive but does not make disk access concurrent, and a cancelled operation
still runs to completion in its thread.

The underlying ``diskcache.Cache`` is thread-safe and not bound to an event
loop, so this adapter reuses the synchronous cache registry directly instead of
the per-loop registry the network backends need.
"""

import asyncio
import logging
import pathlib

from cachetic.extensions.disk import DiskCacheAdapter
from cachetic.types.async_cache_protocol import AsyncCacheProtocol

logger = logging.getLogger(__name__)


class AsyncDiskCacheAdapter(AsyncCacheProtocol):
    """Runs :class:`~cachetic.extensions.disk.DiskCacheAdapter` in a thread pool."""

    _inner: DiskCacheAdapter

    def __init__(self, path: str | pathlib.Path) -> None:
        self._inner = DiskCacheAdapter(path)

    async def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        await asyncio.to_thread(self._inner.set, key, value, ex)

    async def get(self, key: str, /) -> bytes | None:
        return await asyncio.to_thread(self._inner.get, key)

    async def delete(self, key: str, /) -> None:
        await asyncio.to_thread(self._inner.delete, key)

    async def exists(self, key: str, /) -> bool:
        return await asyncio.to_thread(self._inner.exists, key)
