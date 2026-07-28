"""DiskCache adapter for CacheProtocol.

Wraps ``diskcache.Cache`` to provide a unified cache interface. Cache handles
are shared per resolved path through :mod:`cachetic.extensions._registry`, and
released by :func:`cachetic.close_all` — or by
:func:`cachetic.aio.close_all`, which closes this namespace too because the
async disk adapter shares these same synchronous handles.
"""

import logging
import pathlib
import typing

import diskcache

from cachetic.extensions import _registry
from cachetic.types.cache_protocol import CacheProtocol

logger = logging.getLogger(__name__)


class DiskCacheAdapter(CacheProtocol):
    """Adapts ``diskcache.Cache`` to the ``CacheProtocol`` interface.

    Cache instances are shared per resolved path to avoid redundant handles.
    """

    _entry: _registry.EntryHandle

    def __init__(self, path: str | pathlib.Path) -> None:
        resolved: str = str(pathlib.Path(path).resolve())
        self._entry = _registry.EntryHandle(
            _registry.DISK_NAMESPACE,
            resolved,
            factory=lambda: diskcache.Cache(resolved),
            close=_close,
        )

    @property
    def _cache(self) -> diskcache.Cache:
        return self._entry().client

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


def _close(cache: diskcache.Cache) -> None:
    cache.close()
