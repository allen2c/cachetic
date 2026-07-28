"""Asynchronous cache client.

:class:`AsyncCachetic` mirrors :class:`~cachetic.Cachetic` method for method and
inherits the same configuration and value format from
:class:`~cachetic._base.CacheticBase`, so the two clients are interoperable: a
value written by one is readable by the other on every backend.

Backend clients are shared per (event loop, URL). Call :func:`close_all` before
the event loop exits to release them; see its docstring for the caveats.
"""

import asyncio
import logging
import pathlib
import threading
import typing
import urllib.parse

import pydantic

from cachetic._base import MISSING, CacheNotFoundError, CacheticBase, T
from cachetic.extensions.aio._registry import close_all

if typing.TYPE_CHECKING:
    from cachetic.types.async_cache_protocol import AsyncCacheProtocol

__all__ = ["AsyncCachetic", "close_all"]

logger = logging.getLogger("cachetic")


class AsyncCachetic(CacheticBase[T]):
    """A type-safe async cache client for disk, Redis, MongoDB and PostgreSQL.

    Provides automatic serialization/deserialization with configurable TTL.
    """

    # Adapters are cached per event loop, not per instance: a backend client is
    # bound to the loop that created it, so one instance reused across loops
    # (asyncio.run called more than once, a test suite, a worker pool) must not
    # hand back a client belonging to a dead loop.
    _caches: dict[asyncio.AbstractEventLoop, "AsyncCacheProtocol"] = pydantic.PrivateAttr(default_factory=dict)

    # Guards ``_caches``. A threading.Lock rather than an asyncio.Lock for the
    # same reason the async registry uses one: the threads this protects against
    # each run their own loop, and an asyncio.Lock is neither thread-safe nor
    # awaitable from a loop other than the one that created it.
    _caches_lock: threading.Lock = pydantic.PrivateAttr(default_factory=threading.Lock)

    def _build_cache(self) -> "AsyncCacheProtocol":
        """Constructs the backend adapter for ``cache_url``.

        Routes by URL scheme: redis://, mongodb://, postgres://, or a filesystem path.
        """
        if isinstance(self.cache_url, pathlib.Path):
            from cachetic.extensions.aio.disk import AsyncDiskCacheAdapter

            return AsyncDiskCacheAdapter(self.cache_url)

        parsed = urllib.parse.urlparse(self.cache_url)
        if parsed.scheme == "redis":
            try:
                from cachetic.extensions.aio.redis import AsyncRedisCacheAdapter
            except ImportError:
                raise ImportError(
                    "Redis support requires the 'redis' package. Install it with: pip install cachetic[redis]"
                ) from None
            return AsyncRedisCacheAdapter(self.cache_url)
        if parsed.scheme.startswith("mongo"):
            try:
                from cachetic.extensions.aio.mongodb import AsyncMongoCache
            except ImportError:
                raise ImportError(
                    "MongoDB support requires the 'pymongo' package. Install it with: pip install cachetic[mongodb]"
                ) from None
            return AsyncMongoCache(self.cache_url)
        if parsed.scheme.startswith("postgres"):
            try:
                from cachetic.extensions.aio.postgres import AsyncPostgresCache
            except ImportError:
                raise ImportError(
                    "PostgreSQL support requires 'psycopg' and 'psycopg-pool'. "
                    "Install with: pip install cachetic[postgres]"
                ) from None
            return AsyncPostgresCache(self.cache_url)

        from cachetic.extensions.aio.disk import AsyncDiskCacheAdapter

        return AsyncDiskCacheAdapter(self.cache_url)

    async def cache(self) -> "AsyncCacheProtocol":
        """Returns the underlying cache backend for the running event loop.

        Adapter construction needs a running loop, which is why this is a
        coroutine rather than the ``cache`` property the sync client exposes.

        Sweeping dead loops and inserting a fresh one both happen under
        ``_caches_lock``, because one instance shared by a worker pool has a
        thread iterating this dict while another inserts into it. Holding the
        lock is safe: every adapter constructor is synchronous, so it is never
        held across an ``await``. Awaitable setup (index creation,
        ``CREATE TABLE``) happens inside the adapters, guarded by the registry
        entry's own lock.
        """
        loop = asyncio.get_running_loop()

        with self._caches_lock:
            cache = self._caches.get(loop)
            if cache is None:
                for dead in [used for used in self._caches if used.is_closed()]:
                    del self._caches[dead]
                cache = self._build_cache()
                self._caches[loop] = cache
            return cache

    async def get(self, key: str, default: T | None = None) -> T | None:
        """Retrieves and deserializes a value from the cache.

        Args:
            key: Cache key
            default: Returned when the key is absent. Defaults to ``None``.

        ``default`` is returned only for a genuine miss. A cache whose ``T``
        includes ``None`` can store ``None``, and reading it back is a hit —
        it returns ``None``, not ``default``.

        A client with ``default_ttl=0`` is disabled and misses unconditionally.
        """
        if self.disabled:
            return default

        _key = self.get_cache_key(key, with_prefix=True)
        cache = await self.cache()

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[GET] cache: {_key!r}")
        data = await cache.get(_key)

        if data is None:
            return default

        # Load value
        return self._loads_any(data)

    async def get_or_raise(self, key: str) -> T:
        """Retrieves a value from the cache or raises CacheNotFoundError.

        Like :meth:`get`, but a miss raises instead of returning a default.
        Distinguishes a missing key from a stored ``None``, so a cache of an
        optional type does not raise on a key that :meth:`exists` reports.
        """
        out = await self.get(key, default=typing.cast(T, MISSING))
        if out is MISSING:
            raise CacheNotFoundError(f"Cache not found for key '{key}'")
        # `out` may legitimately be None here — a stored None is a hit.
        return typing.cast(T, out)

    async def set(self, key: str, value: T, ex: int | None = None) -> None:
        """Serializes and stores value in cache with optional TTL.

        Args:
            key: Cache key
            value: Value to cache
            ex: TTL in seconds (uses default_ttl if None)

        An effective TTL of 0 drops the write. It does not delete an existing
        entry — the caller asked for this value not to be cached, not for the
        key to be evicted.
        """
        _key = self.get_cache_key(key, with_prefix=True)

        ttl = self._resolve_ttl(ex)
        if ttl == 0:
            return  # No need to set cache

        # Dump value
        _value_bytes = self._dump_any(value)
        cache = await self.cache()

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[SET] cache(ex={ttl}): {_key!r}")
        await cache.set(_key, _value_bytes, self._ttl_to_expiry(ttl))

    async def delete(self, key: str) -> None:
        """Deletes a key-value pair from the cache.

        Runs even on a disabled client: removing a value must not depend on
        whether this client would have written it.
        """
        _key = self.get_cache_key(key, with_prefix=True)
        cache = await self.cache()
        await cache.delete(_key)

    async def exists(self, key: str) -> bool:
        """Checks if a key exists in the cache backend.

        A client with ``default_ttl=0`` is disabled and reports False.
        """
        if self.disabled:
            return False

        _key = self.get_cache_key(key, with_prefix=True)
        cache = await self.cache()
        return await cache.exists(_key)
