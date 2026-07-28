"""A simple, flexible caching library supporting Redis and disk-based storage.

Provides type-safe caching with configurable TTL and automatic serialization.
"""

import base64
import functools
import inspect
import logging
import pathlib
import typing
import urllib.parse

import diskcache
import pydantic
import pydantic_settings
import redis
from rich.pretty import pretty_repr

if typing.TYPE_CHECKING:
    from cachetic.types.cache_protocol import CacheProtocol

T = typing.TypeVar("T")

__version__ = pathlib.Path(__file__).parent.joinpath("VERSION").read_text().strip()


logger = logging.getLogger(__name__)

# New in version 0.6.1: the value format v0.7.0 writes.
#
# v0.7.0 stores every value as a self-describing Data URL. This release does not
# write that format -- a v0.6.0 process must keep being able to read what its
# peers write during a rolling update -- but it reads it, so that a fleet can
# have both versions pointed at one cache at the same time. Upgrade to 0.6.1
# everywhere first, then to 0.7.0; going straight from 0.6.0 to 0.7.0 leaves the
# remaining 0.6.0 pods unable to read anything a 0.7.0 pod has written.
_MIME_BYTES = "application/octet-stream"
_MIME_JSON = "application/json"
_COMPRESSION_NAMES = ("zstd", "zlib")
_COMPRESSION_MARK = b";compression="


class CacheNotFoundError(Exception):
    """Raised when a cache key is not found."""

    pass


class Cachetic(pydantic_settings.BaseSettings, typing.Generic[T]):
    """A type-safe cache client supporting Redis and disk storage.

    Provides automatic serialization/deserialization with configurable TTL.
    """

    model_config = pydantic_settings.SettingsConfigDict(arbitrary_types_allowed=True)

    object_type: pydantic.TypeAdapter[T]

    cache_url: typing.Text | pathlib.Path | redis.Redis | diskcache.Cache
    default_ttl: int = pydantic.Field(
        default=-1,
        description=(
            "Cache time-to-live (seconds). "
            "-1: no expiration. "
            "0: disable cache. "
            ">0: expire after N seconds."
        ),
    )
    prefix: str = pydantic.Field(
        default="",
        description="The prefix of the cache key.",
    )

    # New in version 0.5.0
    compression: bool = pydantic.Field(
        default=False,
        description=(
            "Enable compression for cached values. "
            "When enabled, values are compressed before storage and decompressed on retrieval. "  # noqa: E501
            "Automatic decompression occurs during validation errors if compressed data is detected."  # noqa: E501
        ),
    )

    _is_bytes_type: bool = pydantic.PrivateAttr(default=False)
    _durl_prefixes: typing.Tuple[bytes, ...] = pydantic.PrivateAttr(default=())

    @pydantic.model_validator(mode="after")
    def validate_after_init(self) -> typing.Self:
        """Validates and normalizes fields after model initialization."""
        self.default_ttl = _validate_ttl_value(self.default_ttl)
        self._is_bytes_type = inspect.isclass(self.object_type._type) and issubclass(
            self.object_type._type, bytes
        )
        self._durl_prefixes = _durl_prefixes_for(self._is_bytes_type)
        return self

    @functools.cached_property
    def cache(
        self,
    ) -> typing.Union[diskcache.Cache, redis.Redis, "CacheProtocol"]:
        """Returns the underlying cache instance based on cache_url.

        Automatically creates Redis or DiskCache instances from URLs or paths.
        """
        if isinstance(self.cache_url, redis.Redis):
            return self.cache_url
        if isinstance(self.cache_url, diskcache.Cache):
            return self.cache_url
        if isinstance(self.cache_url, pathlib.Path):
            return diskcache.Cache(self.cache_url)
        if isinstance(self.cache_url, str):
            parsed_path = urllib.parse.urlparse(self.cache_url)
            if parsed_path.scheme == "redis":
                return redis.Redis.from_url(self.cache_url)
            elif parsed_path.scheme.startswith("mongo"):
                from cachetic.extensions.mongodb import MongoCache

                __mongo_cache = MongoCache(self.cache_url)
                return __mongo_cache

            return diskcache.Cache(self.cache_url)

        raise ValueError(f"Unsupported cache url: {self.cache_url}")

    @property
    def cache_url_safe(self) -> str:
        """Returns cache URL with masked credentials for safe logging."""
        from cachetic.utils.hide_url_password import hide_url_password

        return hide_url_password(str(self.cache_url))

    def get_cache_key(self, key: typing.Text, *, with_prefix: bool = True) -> str:
        """Generates cache key with optional prefix.

        Args:
            key: Base cache key
            with_prefix: Whether to include the configured prefix
        """
        return f"{self.prefix}:{key}" if with_prefix and self.prefix else key

    def get(
        self,
        key: typing.Text,
        *args,
        **kwargs,
    ) -> typing.Optional[T]:
        """Retrieves and deserializes value from cache.

        Returns None if key doesn't exist or cache miss occurs.
        """
        _key = self.get_cache_key(key, with_prefix=True)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[GET] cache: {pretty_repr(_key, max_string=40)}")
        data = self.cache.get(_key)

        if data is None:
            return None

        # Load value
        return self._loads_any(data)

    def get_or_raise(
        self,
        key: typing.Text,
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
        key: typing.Text,
        value: T,
        ex: typing.Optional[int] = None,
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

        ex = _validate_ttl_value(ex if ex is not None else self.default_ttl)
        if ex == 0:
            return None  # No need to set cache
        ex_params = None if ex < 0 else ex

        # Dump value
        _value_bytes = self._dump_any(value)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[SET] cache(ex={ex}): {pretty_repr(_key, max_string=40)}")
        self.cache.set(_key, _value_bytes, ex_params)

    def delete(self, key: typing.Text, *args, **kwargs) -> None:
        """Deletes a key-value pair from the cache."""
        _key = self.get_cache_key(key, with_prefix=True)
        self.cache.delete(_key)

    def _validate_any(self, data: typing.Any) -> T:
        if self._is_bytes_type:
            return self.object_type.validate_python(data)
        return self.object_type.validate_json(data)  # type: ignore

    def _loads_any(self, data: typing.Any) -> T:
        """Deserializes bytes from cache, auto-detecting the storage format.

        Two formats are read:

        1. The Data URL v0.7.0 writes -- ``data:<mime>;[compression=<alg>;]base64,``
        2. Everything v0.6.0 and earlier wrote -- compressed or raw bytes

        Detection is deliberately narrow. A ``bytes`` cache stores its payload
        verbatim, so a value written by any version can itself begin with
        ``data:`` -- caching a data URI is an obvious way to end up there. Only
        the exact headers v0.7.0 emits count, and even then an envelope that
        does not decode as base64 falls back to the legacy path rather than
        surfacing as a base64 error on data that was never base64.
        """
        if data is None:
            raise ValueError("Input data must not be None")

        if isinstance(data, bytes) and data.startswith(self._durl_prefixes):
            try:
                header, payload = _split_durl(data)
            except ValueError as durl_error:
                logger.debug(
                    "Value looks like a v0.7.0 Data URL but did not decode, "
                    "falling back to the legacy format. Error: %s",
                    durl_error,
                )
            else:
                return self._loads_durl_payload(header, payload)

        return self._loads_legacy(data)

    def _loads_durl_payload(self, header: bytes, payload: bytes) -> T:
        """Validates a decoded v0.7.0 Data URL payload.

        Deliberately outside the fallback above. Decompression failure means the
        envelope was genuinely v0.7.0's and this process cannot read it -- a
        missing ``zstandard`` where the writer had one -- and that has to be
        raised. Retrying it as legacy data would hand a ``Cachetic[bytes]`` the
        undecoded Data URL as its value, because every byte string validates as
        ``bytes``: a configuration error would become silently wrong data.
        """
        from cachetic.utils.compression import decompress_auto

        if _COMPRESSION_MARK in header:
            payload = decompress_auto(payload)

        return self._validate_any(payload)

    def _loads_legacy(self, data: typing.Any) -> T:
        from cachetic.utils.compression import decompress_auto, might_compressed

        if self.compression:
            data = decompress_auto(data)  # type: ignore

        try:
            return self._validate_any(data)

        except pydantic.ValidationError as e:
            if might_compressed(data):
                logger.warning(
                    "Validation error, but data might be compressed, "
                    "trying to decompress and validate again. "
                    "Error: %s, Data: %s",
                    e,
                    pretty_repr(data, max_string=40),
                )
                data = decompress_auto(data)
                return self._validate_any(data)

            logger.error(f"Validation error: {str(e)}")
            raise e

    def _dump_any(self, value: T) -> bytes:
        from cachetic.utils.compression import compress_auto

        if self._is_bytes_type:
            data_bytes = typing.cast(bytes, self.object_type.validate_python(value))
        else:
            data_bytes = self.object_type.dump_json(value)

        if self.compression:
            # zlib, not "auto". v0.6.0 picks zstd whenever `zstandard` happens
            # to be importable, which makes the algorithm a property of the
            # image rather than of the data -- and this release ships a `zstd`
            # extra, so upgrading to it is exactly when an image is likely to
            # gain that library. A 0.6.1 pod that started writing zstd frames
            # would be unreadable to the 0.6.0 pods it is rolling over, which
            # is the one thing this release exists to prevent. zlib is in the
            # standard library, so every peer can read it: 0.6.0 with or
            # without zstandard, and 0.7.0 either way.
            data_bytes = compress_auto(data_bytes, method="zlib")

        return data_bytes


def _durl_prefixes_for(is_bytes_type: bool) -> typing.Tuple[bytes, ...]:
    """Returns every Data URL header v0.7.0 can write for a value type.

    Has to stay exhaustive and stay in step with v0.7.0's ``_dump_any``: a
    header that release can emit but this does not list reads back as legacy
    data, which fails validation for most types and silently returns the raw
    envelope for ``bytes``.

    The MIME type is part of the match rather than a wildcard because it is
    decided by the reader's own ``object_type``. A ``Cachetic[bytes]`` has no
    business unwrapping an ``application/json`` envelope: under the legacy
    format that is exactly what a JSON document cached as raw bytes looks like.
    """
    mime = _MIME_BYTES if is_bytes_type else _MIME_JSON
    headers = [f"data:{mime};base64,"]
    headers += [
        f"data:{mime};compression={name};base64," for name in _COMPRESSION_NAMES
    ]
    return tuple(header.encode("utf-8") for header in headers)


def _split_durl(data: bytes) -> typing.Tuple[bytes, bytes]:
    """Splits a Data URL into its header and decoded payload.

    Raises ``ValueError`` when the payload is not base64, which is the caller's
    signal that this was never v0.7.0's format despite the matching header.
    ``validate=True`` is what makes that signal reliable: the default silently
    discards non-alphabet bytes and would decode almost anything into garbage.
    """
    header, separator, encoded = data.partition(b",")
    if not separator:
        raise ValueError("Data URL has no payload separator")
    return header, base64.b64decode(encoded, validate=True)


def _validate_ttl_value(ttl: int) -> int:
    """Validates and normalizes TTL values.

    Ensures TTL is either -1 (no expiration) or positive integer.
    """
    if ttl < 0:
        return -1
    return ttl
