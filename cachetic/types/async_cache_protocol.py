"""Structural protocol for asynchronous cache backends.

Mirrors :class:`~cachetic.types.cache_protocol.CacheProtocol` operation for
operation, so the sync and async clients differ only in how they await I/O.
Positional-only `/` enables structural subtyping regardless of parameter names.

Backends that need schema or index setup do it lazily inside their own
operations rather than exposing it here, keeping both protocols identical in
shape. Connection teardown is process-wide and lives in
:func:`cachetic.aio.close_all`, because clients are shared between instances.
"""

import typing


class AsyncCacheProtocol(typing.Protocol):
    """Async cache backend interface: get, set, delete, exists.

    ``await AsyncCachetic.cache()`` returns one of these, so every method here is
    reachable by a caller and every backend has to answer it identically. See
    :mod:`cachetic.extensions._ttl` for what ``ex`` means.
    """

    async def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        """Stores ``value``. ``ex`` is seconds; ``None`` or non-positive never expires."""
        ...

    async def get(self, key: str, /) -> bytes | None: ...

    async def delete(self, key: str, /) -> None: ...

    async def exists(self, key: str, /) -> bool: ...
