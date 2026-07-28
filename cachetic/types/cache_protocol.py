"""Structural protocol for cache backends.

All backends (DiskCache, Redis, MongoDB, Postgres) implement this protocol.
Positional-only `/` enables structural subtyping regardless of parameter names.
"""

import typing


class CacheProtocol(typing.Protocol):
    """Cache backend interface: get, set, delete, exists.

    ``Cachetic.cache`` returns one of these, so every method here is reachable
    by a caller and every backend has to answer it identically. ``ex`` is the
    place that took work: see :mod:`cachetic.extensions._ttl` for the contract
    and for what the four drivers did before it was imposed.
    """

    def set(self, key: str, value: bytes, ex: int | None = None, /) -> None:
        """Stores ``value``. ``ex`` is seconds; ``None`` or non-positive never expires."""
        ...

    def get(self, key: str, /) -> bytes | None: ...

    def delete(self, key: str, /) -> None: ...

    def exists(self, key: str, /) -> bool: ...
