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

from cachetic._base import (
    MISSING,
    CacheNotFoundError,
    CacheticBase,
    T,
    driver_modules,
    is_async_redis,
    warn_ignored_arguments,
)
from cachetic.extensions.aio._registry import close_all

if typing.TYPE_CHECKING:
    from cachetic.types.async_cache_protocol import AsyncCacheProtocol

__all__ = ["AsyncCachetic", "close_all"]

logger = logging.getLogger("cachetic")


def _wrap_supplied_client(client: typing.Any) -> "AsyncCacheProtocol":
    """Adapts a backend client the caller built, without registering it.

    The async twin of :func:`cachetic._wrap_supplied_client`, with one real
    difference: Redis has to be a ``redis.asyncio.Redis``. A synchronous
    ``redis.Redis`` would block the event loop on every call, so it is refused
    rather than quietly accepted. ``diskcache.Cache`` is the same object on both
    sides — it has no async API and runs in a worker thread either way.

    v0.6.0 had no async client, so nothing here is grandfathered; it exists so
    that what ``Cachetic`` accepts, ``AsyncCachetic`` accepts too.
    """
    modules = driver_modules(client)
    top_level = {module.split(".")[0] for module in modules}

    if "diskcache" in top_level:
        from cachetic.extensions.aio.disk import AsyncDiskCacheAdapter

        return AsyncDiskCacheAdapter(client=client)

    if is_async_redis(modules):
        from cachetic.extensions.aio.redis import AsyncRedisCacheAdapter

        return AsyncRedisCacheAdapter(client=client)

    if "redis" in top_level:
        raise TypeError(
            "AsyncCachetic got a synchronous redis.Redis, which would block the "
            "event loop on every call. Pass a redis.asyncio.Redis instead."
        )

    raise TypeError(
        f"cache_url got a {type(client).__module__}.{type(client).__qualname__}. "
        "A prebuilt client may be a redis.asyncio.Redis or a diskcache.Cache; "
        "MongoDB and PostgreSQL are configured by URL because their adapters also "
        "need ?collection= / ?table=."
    )


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
        if self.cache_client is not None:
            return _wrap_supplied_client(self.cache_client)

        if isinstance(self.cache_url, pathlib.Path):
            from cachetic.extensions.aio.disk import AsyncDiskCacheAdapter

            return AsyncDiskCacheAdapter(self.cache_url)

        # ``accept_a_live_backend_client`` replaced any non-(str, Path)
        # ``cache_url`` with a label before this ran, so what is left is a URL.
        url: str = typing.cast(str, self.cache_url)

        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == "redis":
            try:
                from cachetic.extensions.aio.redis import AsyncRedisCacheAdapter
            except ImportError:
                raise ImportError(
                    "Redis support requires the 'redis' package. Install it with: pip install cachetic[redis]"
                ) from None
            return AsyncRedisCacheAdapter(url)
        if parsed.scheme.startswith("mongo"):
            try:
                from cachetic.extensions.aio.mongodb import AsyncMongoCache
            except ImportError:
                raise ImportError(
                    "MongoDB support requires the 'pymongo' package. Install it with: pip install cachetic[mongodb]"
                ) from None
            return AsyncMongoCache(url)
        if parsed.scheme.startswith("postgres"):
            try:
                from cachetic.extensions.aio.postgres import AsyncPostgresCache
            except ImportError:
                raise ImportError(
                    "PostgreSQL support requires 'psycopg' and 'psycopg-pool'. "
                    "Install with: pip install cachetic[postgres]"
                ) from None
            return AsyncPostgresCache(url)

        from cachetic.extensions.aio.disk import AsyncDiskCacheAdapter

        return AsyncDiskCacheAdapter(url)

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

    async def get(self, key: str, default: T | None = None, *args: typing.Any, **kwargs: typing.Any) -> T | None:
        """Retrieves and deserializes a value from the cache.

        Args:
            key: Cache key
            default: Returned when the key is absent. Defaults to ``None``.

        ``default`` is returned only for a genuine miss. A cache whose ``T``
        includes ``None`` can store ``None``, and reading it back is a hit —
        it returns ``None``, not ``default``.

        A client with ``default_ttl=0`` is disabled and misses unconditionally.

        Extra arguments are accepted and ignored with a ``DeprecationWarning``;
        see :func:`cachetic._base.warn_ignored_arguments` for why they have to be.
        """
        warn_ignored_arguments("get", args, kwargs)

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

    async def get_or_raise(self, key: str, *args: typing.Any, **kwargs: typing.Any) -> T:
        """Retrieves a value from the cache or raises CacheNotFoundError.

        Like :meth:`get`, but a miss raises instead of returning a default.
        Distinguishes a missing key from a stored ``None``, so a cache of an
        optional type does not raise on a key that :meth:`exists` reports.

        Extra arguments are accepted and ignored with a ``DeprecationWarning``;
        see :func:`cachetic._base.warn_ignored_arguments` for why they have to be.
        """
        warn_ignored_arguments("get_or_raise", args, kwargs)

        out = await self.get(key, default=typing.cast(T, MISSING))
        if out is MISSING:
            raise CacheNotFoundError(f"Cache not found for key '{key}'")
        # `out` may legitimately be None here — a stored None is a hit.
        return typing.cast(T, out)

    async def set(self, key: str, value: T, ex: int | None = None, *args: typing.Any, **kwargs: typing.Any) -> None:
        """Serializes and stores value in cache with optional TTL.

        Args:
            key: Cache key
            value: Value to cache
            ex: TTL in seconds (uses default_ttl if None)

        An effective TTL of 0 drops the write. It does not delete an existing
        entry — the caller asked for this value not to be cached, not for the
        key to be evicted.

        A client with ``default_ttl=0`` is disabled and drops the write whatever
        ``ex`` says. Without that check an explicit ``ex`` would resolve on its
        own and write a value this same client's :meth:`get` refuses to return.

        Extra arguments are accepted and ignored with a ``DeprecationWarning``;
        see :func:`cachetic._base.warn_ignored_arguments` for why they have to be.
        """
        warn_ignored_arguments("set", args, kwargs)

        if self.disabled:
            return

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

    async def delete(self, key: str, *args: typing.Any, **kwargs: typing.Any) -> None:
        """Deletes a key-value pair from the cache.

        Runs even on a disabled client: removing a value must not depend on
        whether this client would have written it.

        Extra arguments are accepted and ignored with a ``DeprecationWarning``;
        see :func:`cachetic._base.warn_ignored_arguments` for why they have to be.
        """
        warn_ignored_arguments("delete", args, kwargs)

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
