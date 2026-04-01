"""Structural protocol for cache backends.

All backends (DiskCache, Redis, MongoDB, Postgres) implement this protocol.
Positional-only `/` enables structural subtyping regardless of parameter names.
"""

import typing


class CacheProtocol(typing.Protocol):
    """Cache backend interface: get, set, delete, exists, clear."""

    def set(self, key: str, value: bytes, ex: int | None = None, /) -> None: ...

    def get(self, key: str, /) -> bytes | None: ...

    def delete(self, key: str, /) -> None: ...

    def exists(self, key: str, /) -> bool: ...

    def clear(self) -> None: ...
