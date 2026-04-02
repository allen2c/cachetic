"""DiskCache adapter for CacheProtocol.

Wraps ``diskcache.Cache`` to provide a unified cache interface.
Reuses Cache instances per resolved path via a module-level registry.
"""

import logging
import pathlib
import typing

import diskcache

from cachetic.types.cache_protocol import CacheProtocol

logger = logging.getLogger(__name__)

_cache_registry: dict[str, diskcache.Cache] = {}


class DiskCacheAdapter(CacheProtocol):
    """Adapts ``diskcache.Cache`` to the ``CacheProtocol`` interface.

    Cache instances are shared per resolved path to avoid redundant handles.
    """

    _cache: diskcache.Cache

    def __init__(self, path: str | pathlib.Path) -> None:
        resolved: str = str(pathlib.Path(path).resolve())
        if resolved in _cache_registry:
            self._cache = _cache_registry[resolved]
            logger.debug("Reusing existing DiskCache for: %s", resolved)
        else:
            self._cache = diskcache.Cache(resolved)
            _cache_registry[resolved] = self._cache
            logger.debug("Created new DiskCache for: %s", resolved)

    def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        self._cache.set(key, value, expire=ex)

    def get(self, key: str, /) -> bytes | None:
        result: typing.Any = self._cache.get(key)
        if result is None:
            return None
        return typing.cast(bytes, result)

    def delete(self, key: str, /) -> None:
        self._cache.delete(key)

    def exists(self, key: str, /) -> bool:
        return key in self._cache
