"""A simple, flexible caching library supporting Redis and disk-based storage.

Provides type-safe caching with configurable TTL and automatic serialization.
"""

import functools
import inspect
import logging
import pathlib
import typing
import urllib.parse

import pydantic
import pydantic_settings

if typing.TYPE_CHECKING:
    from cachetic.types.cache_protocol import CacheProtocol

T = typing.TypeVar("T")

__version__ = pathlib.Path(__file__).parent.joinpath("VERSION").read_text().strip()


logger = logging.getLogger(__name__)


class CacheNotFoundError(Exception):
    """Raised when a cache key is not found."""

    pass


class Cachetic(pydantic_settings.BaseSettings, typing.Generic[T]):
    """A type-safe cache client supporting Redis and disk storage.

    Provides automatic serialization/deserialization with configurable TTL.
    """

    model_config = pydantic_settings.SettingsConfigDict(arbitrary_types_allowed=True)

    object_type: pydantic.TypeAdapter[T]

    cache_url: str | pathlib.Path
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

    @pydantic.model_validator(mode="after")
    def validate_after_init(self) -> typing.Self:
        """Validates and normalizes fields after model initialization."""
        self.default_ttl = _validate_ttl_value(self.default_ttl)
        self._is_bytes_type = inspect.isclass(self.object_type._type) and issubclass(
            self.object_type._type, bytes
        )
        return self

    @functools.cached_property
    def cache(self) -> "CacheProtocol":
        """Returns the underlying cache backend as a CacheProtocol.

        Routes by URL scheme: redis://, mongodb://, or filesystem path.
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
                    "PostgreSQL support requires 'peewee' and 'psycopg'. "
                    "Install with: pip install cachetic[postgres]"
                ) from None
            return PostgresCache(self.cache_url)

        from cachetic.extensions.disk import DiskCacheAdapter

        return DiskCacheAdapter(self.cache_url)

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
            logger.debug(f"[GET] cache: {repr(_key)}")
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
            logger.debug(f"[SET] cache(ex={ex}): {repr(_key)}")
        self.cache.set(_key, _value_bytes, ex_params)

    def delete(self, key: typing.Text, *args, **kwargs) -> None:
        """Deletes a key-value pair from the cache."""
        _key = self.get_cache_key(key, with_prefix=True)
        self.cache.delete(_key)

    def exists(self, key: typing.Text) -> bool:
        """Checks if a key exists in the cache backend."""
        _key = self.get_cache_key(key, with_prefix=True)
        return self.cache.exists(_key)

    def _validate_any(self, data: typing.Any) -> T:
        if self._is_bytes_type:
            return self.object_type.validate_python(data)
        return self.object_type.validate_json(data)  # type: ignore

    def _loads_any(self, data: bytes) -> T:
        """Deserializes bytes from cache, auto-detecting the storage format.

        Supports three formats:
        1. Data URL (v0.7.0+): b"data:..." — parse DURL, extract payload
        2. Compressed legacy (v0.5.0+): zstd/zlib magic bytes — decompress
        3. Raw bytes legacy (v0.1.0+): plain JSON bytes or raw bytes
        """
        if data is None:
            raise ValueError("Input data must not be None")

        # Path 1: Data URL format (v0.7.0+)
        if data.startswith(b"data:"):
            return self._loads_durl(data)

        # Path 2 & 3: Legacy formats (compressed or raw)
        return self._loads_legacy(data)

    def _loads_durl(self, data: bytes) -> T:
        """Parses a Data URL value and returns the deserialized object."""
        from durl import DURL

        durl: DURL = DURL(data.decode("utf-8"))
        payload: bytes = typing.cast(bytes, durl.parsed_data)

        compression_alg: str | None = durl.parameters.get("compression")
        if compression_alg is not None:
            from cachetic.utils.compression import decompress_auto

            payload = decompress_auto(payload)

        return self._validate_any(payload)

    def _loads_legacy(self, data: bytes) -> T:
        """Handles legacy formats: compressed bytes or raw JSON/bytes."""
        from cachetic.utils.compression import decompress_auto, might_compressed

        if self.compression:
            data = decompress_auto(data)

        try:
            return self._validate_any(data)
        except pydantic.ValidationError as e:
            if might_compressed(data):
                logger.warning(
                    "Validation error, but data might be compressed, "
                    "trying to decompress and validate again. "
                    "Error: %s, Data: %s",
                    e,
                    repr(data),
                )
                data = decompress_auto(data)
                return self._validate_any(data)

            logger.error(f"Validation error: {str(e)}")
            raise e

    def _dump_any(self, value: T) -> bytes:
        """Serializes value into a Data URL encoded as UTF-8 bytes.

        Format: data:<mime>;[compression=<alg>;]base64,<payload>
        """
        from durl import DURL

        if self._is_bytes_type:
            mime_type: str = "application/octet-stream"
            data_bytes: bytes = typing.cast(
                bytes, self.object_type.validate_python(value)
            )
        else:
            mime_type = "application/json"
            data_bytes = self.object_type.dump_json(value)

        parameters: dict[str, str] = {}
        if self.compression:
            from cachetic.utils.compression import compress_auto

            data_bytes = compress_auto(data_bytes)
            parameters["compression"] = _detect_compression_name(data_bytes)

        durl: DURL = DURL.build(
            mime_type=mime_type,
            data=data_bytes,
            parameters=parameters or None,
        )
        return str(durl).encode("utf-8")


def _detect_compression_name(data: bytes) -> str:
    """Returns compression algorithm name by inspecting magic bytes."""
    from cachetic.utils.compression import ZSTD_MAGIC

    if data.startswith(ZSTD_MAGIC):
        return "zstd"
    return "zlib"


def _validate_ttl_value(ttl: int) -> int:
    """Validates and normalizes TTL values.

    Ensures TTL is either -1 (no expiration) or positive integer.
    """
    if ttl < 0:
        return -1
    return ttl
