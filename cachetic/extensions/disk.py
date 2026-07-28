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

from cachetic.extensions import _registry, _ttl
from cachetic.types.cache_protocol import CacheProtocol

logger = logging.getLogger(__name__)


class DiskCacheAdapter(CacheProtocol):
    """Adapts ``diskcache.Cache`` to the ``CacheProtocol`` interface.

    Cache instances are shared per resolved path to avoid redundant handles.
    """

    _entry: _registry.EntryHandle | None

    def __init__(self, path: str | pathlib.Path | None = None, *, client: diskcache.Cache | None = None) -> None:
        """Wraps a shared cache for ``path``, or a ``diskcache.Cache`` given to it.

        A supplied client is used as-is and never registered: the registry keys
        on a URL, this has none, and :func:`cachetic.close_all` must not close a
        handle it did not open. See ``CacheticBase.accept_a_live_backend_client``.
        """
        if client is not None:
            self._entry = None
            self._supplied = client
            return
        if path is None:
            raise ValueError("DiskCacheAdapter needs either a path or a client")

        resolved: str = str(pathlib.Path(path).resolve())
        self._entry = _registry.EntryHandle(
            _registry.DISK_NAMESPACE,
            resolved,
            factory=lambda: diskcache.Cache(resolved),
            close=_close,
        )

    @property
    def _cache(self) -> diskcache.Cache:
        if self._entry is None:
            return self._supplied
        return self._entry().client

    def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        self._cache.set(key, value, expire=_ttl.expiry_seconds(ex))

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
