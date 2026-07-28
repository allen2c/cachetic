"""Type-safe caching for Python with Pydantic serialization.

Supports disk, Redis, MongoDB and PostgreSQL backends behind one interface, in
both synchronous (:class:`Cachetic`) and asynchronous (:class:`AsyncCachetic`)
flavours. Both clients share a single value format, so a cache written by one is
readable by the other.

Backend clients are shared per URL between every instance that uses it. Call
:func:`close_all` to release them — and :func:`cachetic.aio.close_all` for the
async ones, which are shared per event loop and have to be closed from theirs.
"""

import functools
import logging
import pathlib
import typing
import urllib.parse

from cachetic._base import (  # noqa: F401  (the two underscored names are re-exports kept for compatibility)
    MISSING,
    CacheNotFoundError,
    CacheticBase,
    T,
    _detect_compression_name,
    _validate_ttl_value,
    driver_modules,
    is_async_redis,
    warn_ignored_arguments,
)
from cachetic.extensions._registry import close_all

if typing.TYPE_CHECKING:
    from cachetic.aio import AsyncCachetic
    from cachetic.types.cache_protocol import CacheProtocol

__all__ = [
    "AsyncCachetic",
    "CacheNotFoundError",
    "Cachetic",
    "CacheticBase",
    "__version__",
    "close_all",
]

__version__ = pathlib.Path(__file__).parent.joinpath("VERSION").read_text().strip()


logger = logging.getLogger("cachetic")


class Cachetic(CacheticBase[T]):
    """A type-safe cache client supporting disk, Redis, MongoDB and PostgreSQL.

    Provides automatic serialization/deserialization with configurable TTL.
    """

    @functools.cached_property
    def cache(self) -> "CacheProtocol":
        """Returns the underlying cache backend as a CacheProtocol.

        Routes by URL scheme: redis://, mongodb://, postgres://, or a filesystem path.

        This is an adapter, not the driver object. v0.6.0 handed back the real
        ``redis.Redis`` / ``diskcache.Cache``; [Principle 1](../docs/PRINCIPLES.md)
        names this as the one thing it does not cover, because
        [Principle 2](../docs/PRINCIPLES.md) has nothing to stand on without it.
        A client you supplied yourself comes back wrapped too — keep your own
        reference if you need the driver's own methods.
        """
        if self.cache_client is not None:
            return _wrap_supplied_client(self.cache_client)

        if isinstance(self.cache_url, pathlib.Path):
            from cachetic.extensions.disk import DiskCacheAdapter

            return DiskCacheAdapter(self.cache_url)

        # ``accept_a_live_backend_client`` replaced any non-(str, Path)
        # ``cache_url`` with a label before this ran, so what is left is a URL.
        url: str = typing.cast(str, self.cache_url)

        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == "redis":
            try:
                from cachetic.extensions.redis import RedisCacheAdapter
            except ImportError:
                raise ImportError(
                    "Redis support requires the 'redis' package. Install it with: pip install cachetic[redis]"
                ) from None
            return RedisCacheAdapter(url)
        if parsed.scheme.startswith("mongo"):
            try:
                from cachetic.extensions.mongodb import MongoCache
            except ImportError:
                raise ImportError(
                    "MongoDB support requires the 'pymongo' package. Install it with: pip install cachetic[mongodb]"
                ) from None
            return MongoCache(url)
        if parsed.scheme.startswith("postgres"):
            try:
                from cachetic.extensions.postgres import PostgresCache
            except ImportError:
                raise ImportError(
                    "PostgreSQL support requires 'psycopg' and 'psycopg-pool'. "
                    "Install with: pip install cachetic[postgres]"
                ) from None
            return PostgresCache(url)

        from cachetic.extensions.disk import DiskCacheAdapter

        return DiskCacheAdapter(url)

    def get(self, key: str, default: T | None = None, *args: typing.Any, **kwargs: typing.Any) -> T | None:
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

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[GET] cache: {_key!r}")
        data = self.cache.get(_key)

        if data is None:
            return default

        # Load value
        return self._loads_any(data)

    def get_or_raise(self, key: str, *args: typing.Any, **kwargs: typing.Any) -> T:
        """Retrieves a value from the cache or raises CacheNotFoundError.

        Like :meth:`get`, but a miss raises instead of returning a default.
        Distinguishes a missing key from a stored ``None``, so a cache of an
        optional type does not raise on a key that :meth:`exists` reports.

        Extra arguments are accepted and ignored with a ``DeprecationWarning``;
        see :func:`cachetic._base.warn_ignored_arguments` for why they have to be.
        """
        warn_ignored_arguments("get_or_raise", args, kwargs)

        out = self.get(key, default=typing.cast(T, MISSING))
        if out is MISSING:
            raise CacheNotFoundError(f"Cache not found for key '{key}'")
        # `out` may legitimately be None here — a stored None is a hit.
        return typing.cast(T, out)

    def set(self, key: str, value: T, ex: int | None = None, *args: typing.Any, **kwargs: typing.Any) -> None:
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

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[SET] cache(ex={ttl}): {_key!r}")
        self.cache.set(_key, _value_bytes, self._ttl_to_expiry(ttl))

    def delete(self, key: str, *args: typing.Any, **kwargs: typing.Any) -> None:
        """Deletes a key-value pair from the cache.

        Runs even on a disabled client: removing a value must not depend on
        whether this client would have written it.

        Extra arguments are accepted and ignored with a ``DeprecationWarning``;
        see :func:`cachetic._base.warn_ignored_arguments` for why they have to be.
        """
        warn_ignored_arguments("delete", args, kwargs)

        _key = self.get_cache_key(key, with_prefix=True)
        self.cache.delete(_key)

    def exists(self, key: str) -> bool:
        """Checks if a key exists in the cache backend.

        A client with ``default_ttl=0`` is disabled and reports False.
        """
        if self.disabled:
            return False

        _key = self.get_cache_key(key, with_prefix=True)
        return self.cache.exists(_key)


def _wrap_supplied_client(client: typing.Any) -> "CacheProtocol":
    """Adapts a backend client the caller built, without registering it.

    Recognition is by module name rather than ``isinstance`` so that this stays
    free of a top-level ``import redis`` — [Principle 5](../docs/PRINCIPLES.md).

    Only Redis and disk are supported, and that is not an oversight to be filled
    in later: a ``MongoClient`` or a psycopg pool does not carry the collection
    or table name, which reaches those adapters through ``?collection=`` /
    ``?table=`` on the URL. There is nothing to route a bare client to. This is
    the difference [Principle 2](../docs/PRINCIPLES.md) means by one that cannot
    be removed, so it is written down here and in the README rather than left to
    be discovered.
    """
    modules = driver_modules(client)
    top_level = {module.split(".")[0] for module in modules}

    if "diskcache" in top_level:
        from cachetic.extensions.disk import DiskCacheAdapter

        return DiskCacheAdapter(client=client)

    if is_async_redis(modules):
        raise TypeError(
            "Cachetic got a redis.asyncio client. Its calls return coroutines "
            "this client would never await, so every write would silently do "
            "nothing. Pass a redis.Redis, or use AsyncCachetic."
        )

    if "redis" in top_level:
        from cachetic.extensions.redis import RedisCacheAdapter

        return RedisCacheAdapter(client=client)

    raise TypeError(
        f"cache_url got a {type(client).__module__}.{type(client).__qualname__}. "
        "A prebuilt client may be a redis.Redis or a diskcache.Cache; MongoDB and "
        "PostgreSQL are configured by URL because their adapters also need "
        "?collection= / ?table=."
    )


def __getattr__(name: str) -> typing.Any:
    """Lazily exposes ``AsyncCachetic`` without importing asyncio at import time."""
    if name == "AsyncCachetic":
        from cachetic.aio import AsyncCachetic

        return AsyncCachetic
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
