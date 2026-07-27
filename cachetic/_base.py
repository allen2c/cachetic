"""Backend-agnostic core shared by the sync and async clients.

Everything here is pure: configuration, key naming, TTL normalisation, and the
serialisation format. No I/O happens in this module, which is what lets
``Cachetic`` and ``AsyncCachetic`` share a single implementation of the wire
format instead of maintaining two copies of it.
"""

import inspect
import logging
import pathlib
import typing

import pydantic
import pydantic_settings

T = typing.TypeVar("T")

# Deliberately not ``__name__``: this module is an implementation detail, and
# downstream code configures logging via the ``cachetic`` logger.
logger = logging.getLogger("cachetic")


class CacheNotFoundError(Exception):
    """Raised when a cache key is not found."""


class CacheticBase(pydantic_settings.BaseSettings, typing.Generic[T]):
    """Configuration and serialisation shared by every Cachetic client.

    Subclasses supply the backend wiring and the four I/O operations; this class
    owns the value format so sync and async clients stay interoperable.
    """

    model_config = pydantic_settings.SettingsConfigDict(
        arbitrary_types_allowed=True,
        env_prefix="CACHETIC_",
    )

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

    @property
    def cache_url_safe(self) -> str:
        """Returns cache URL with masked credentials for safe logging."""
        from cachetic.utils.hide_url_password import hide_url_password

        return hide_url_password(str(self.cache_url))

    def get_cache_key(self, key: str, *, with_prefix: bool = True) -> str:
        """Generates cache key with optional prefix.

        Args:
            key: Base cache key
            with_prefix: Whether to include the configured prefix
        """
        return f"{self.prefix}:{key}" if with_prefix and self.prefix else key

    def _resolve_ttl(self, ex: int | None) -> int:
        """Normalises a per-call TTL against ``default_ttl``.

        Returns -1 (no expiration), 0 (caller must skip the write), or a positive
        number of seconds.
        """
        return _validate_ttl_value(ex if ex is not None else self.default_ttl)

    @staticmethod
    def _ttl_to_expiry(ttl: int) -> int | None:
        """Converts a normalised TTL into the ``ex`` argument backends expect.

        Expiry precision depends on the backend. Redis and diskcache enforce the
        deadline themselves, but the MongoDB and PostgreSQL backends store a
        whole-second deadline derived from a truncated clock and compare it with
        a strict ``<``, so an entry there can outlive its TTL by up to a second
        (``ex=1`` lives for one to two seconds). Cachetic treats expiring late as
        acceptable for a cache: keeping the sync and async backends bit-for-bit
        identical matters more than sub-second accuracy.
        """
        return None if ttl < 0 else ttl

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

            logger.error(f"Validation error: {e!s}")
            raise

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
