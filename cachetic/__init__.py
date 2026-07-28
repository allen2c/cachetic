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

from cachetic._base import (  # noqa: F401  (re-exported for backwards compatibility)
    CacheNotFoundError,
    CacheticBase,
    T,
    _detect_compression_name,
    _validate_ttl_value,
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
        """
        if isinstance(self.cache_url, pathlib.Path):
            from cachetic.extensions.disk import DiskCacheAdapter

            return DiskCacheAdapter(self.cache_url)

        parsed = urllib.parse.urlparse(self.cache_url)
        if parsed.scheme == "redis":
            try:
                from cachetic.extensions.redis import RedisCacheAdapter
            except ImportError:
                raise ImportError(
                    "Redis support requires the 'redis' package. "
                    "Install it with: pip install cachetic[redis]"
                ) from None
            return RedisCacheAdapter(self.cache_url)
        if parsed.scheme.startswith("mongo"):
            try:
                from cachetic.extensions.mongodb import MongoCache
            except ImportError:
                raise ImportError(
                    "MongoDB support requires the 'pymongo' package. "
                    "Install it with: pip install cachetic[mongodb]"
                ) from None
            return MongoCache(self.cache_url)
        if parsed.scheme.startswith("postgres"):
            try:
                from cachetic.extensions.postgres import PostgresCache
            except ImportError:
                raise ImportError(
                    "PostgreSQL support requires 'psycopg' and 'psycopg-pool'. "
                    "Install with: pip install cachetic[postgres]"
                ) from None
            return PostgresCache(self.cache_url)

        from cachetic.extensions.disk import DiskCacheAdapter

        return DiskCacheAdapter(self.cache_url)

    def get(
        self,
        key: str,
        *args,
        **kwargs,
    ) -> T | None:
        """Retrieves and deserializes value from cache.

        Returns None if key doesn't exist or cache miss occurs.
        """
        _key = self.get_cache_key(key, with_prefix=True)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[GET] cache: {_key!r}")
        data = self.cache.get(_key)

        if data is None:
            return None

        # Load value
        return self._loads_any(data)

    def get_or_raise(
        self,
        key: str,
        *args,
        **kwargs,
    ) -> T:
        """Retrieves value from cache or raises CacheNotFoundError.

        Similar to get() but throws exception instead of returning None.
        """
        out = self.get(key, *args, **kwargs)
        if out is None:
            raise CacheNotFoundError(f"Cache not found for key '{key}'")
        return out

    def set(
        self,
        key: str,
        value: T,
        ex: int | None = None,
        *args,
        **kwargs,
    ) -> None:
        """Serializes and stores value in cache with optional TTL.

        Args:
            key: Cache key
            value: Value to cache
            ex: TTL in seconds (uses default_ttl if None)
        """
        _key = self.get_cache_key(key, with_prefix=True)

        ttl = self._resolve_ttl(ex)
        if ttl == 0:
            return  # No need to set cache

        # Dump value
        _value_bytes = self._dump_any(value)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[SET] cache(ex={ttl}): {_key!r}")
        self.cache.set(_key, _value_bytes, self._ttl_to_expiry(ttl))

    def delete(self, key: str, *args, **kwargs) -> None:
        """Deletes a key-value pair from the cache."""
        _key = self.get_cache_key(key, with_prefix=True)
        self.cache.delete(_key)

    def exists(self, key: str) -> bool:
        """Checks if a key exists in the cache backend."""
        _key = self.get_cache_key(key, with_prefix=True)
        return self.cache.exists(_key)


def __getattr__(name: str) -> typing.Any:
    """Lazily exposes ``AsyncCachetic`` without importing asyncio at import time."""
    if name == "AsyncCachetic":
        from cachetic.aio import AsyncCachetic

        return AsyncCachetic
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
