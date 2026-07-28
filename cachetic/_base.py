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

_MIME_BYTES = "application/octet-stream"
_MIME_JSON = "application/json"

_ZSTD = "zstd"
_ZLIB = "zlib"
_COMPRESSION_NAMES = (_ZSTD, _ZLIB)

MISSING: typing.Final = object()
"""Sentinel for "the backend had no entry", as distinct from a stored ``None``.

``get`` returns its ``default`` only for a real miss. A cache whose ``T``
includes ``None`` can legitimately store ``None``, and that is a hit — without
this sentinel ``get_or_raise`` would raise on a key that ``exists`` reports as
present.
"""


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
            "-1 (the default): store without expiry. "
            "0: turn this client off — reads miss and writes are dropped. "
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
            "Compress values before storing them. "
            "Values written from version 0.7.0 on record the algorithm they used, "
            "so reads decompress correctly whatever this flag is set to. "
            "It only decides what new writes do."
        ),
    )

    _is_bytes_type: bool = pydantic.PrivateAttr(default=False)
    _durl_prefixes: tuple[bytes, ...] = pydantic.PrivateAttr(default=())

    @pydantic.model_validator(mode="after")
    def validate_after_init(self) -> typing.Self:
        """Validates and normalizes fields after model initialization."""
        self.default_ttl = _validate_ttl_value(self.default_ttl)
        self._is_bytes_type = inspect.isclass(self.object_type._type) and issubclass(self.object_type._type, bytes)
        self._durl_prefixes = _durl_prefixes_for(self._is_bytes_type)
        return self

    @property
    def cache_url_safe(self) -> str:
        """Returns cache URL with masked credentials for safe logging."""
        from cachetic.utils.hide_url_password import hide_url_password

        return hide_url_password(str(self.cache_url))

    @property
    def disabled(self) -> bool:
        """True when ``default_ttl=0`` has turned this client off.

        A disabled client misses on every read and drops every write, which is
        what lets a deployment switch caching off from configuration alone. It
        does not evict: values another client wrote stay where they are, and a
        per-call ``ex=0`` still means "do not store *this* value" rather than
        "remove what is already there".
        """
        return self.default_ttl == 0

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

        Detection is deliberately narrow. A ``bytes`` cache stores its payload
        verbatim, so a value written before 0.7.0 can itself start with
        ``data:`` — a cached data URI is an obvious way to end up there. Only the
        exact headers :meth:`_dump_any` emits count as the new format, and even
        then a parse failure falls back to the legacy path rather than surfacing
        as a base64 error on data that was never base64.
        """
        # Path 1: Data URL format (v0.7.0+)
        if data.startswith(self._durl_prefixes):
            try:
                return self._loads_durl(data)
            except ValueError as durl_error:
                logger.debug(
                    "Value looks like a Cachetic Data URL but did not parse, "
                    "falling back to the pre-0.7.0 format. Error: %s",
                    durl_error,
                )
                try:
                    return self._loads_legacy(data)
                except Exception:
                    raise durl_error from None

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
        """Handles legacy formats: compressed bytes or raw JSON/bytes.

        The "retry after decompressing" recovery below keys off a validation
        error, so it cannot help a ``bytes`` cache: every byte string is a valid
        ``bytes``, compressed or not. Reading pre-0.7.0 ``bytes`` data therefore
        requires ``compression`` to be set the way it was when that data was
        written. Values written from 0.7.0 on are self-describing and have no
        such requirement.
        """
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

        mime_type: str = _mime_for(self._is_bytes_type)
        if self._is_bytes_type:
            data_bytes: bytes = typing.cast(bytes, self.object_type.validate_python(value))
        else:
            data_bytes = self.object_type.dump_json(value)

        parameters: dict[str, str] = {}
        # An empty payload compresses to itself, with no magic bytes to name.
        if self.compression and data_bytes:
            from cachetic.utils.compression import compress_auto

            data_bytes = compress_auto(data_bytes)
            parameters["compression"] = _detect_compression_name(data_bytes)

        durl: DURL = DURL.build(
            mime_type=mime_type,
            data=data_bytes,
            parameters=parameters or None,
        )
        return str(durl).encode("utf-8")


def _mime_for(is_bytes_type: bool) -> str:
    """Returns the MIME type :meth:`CacheticBase._dump_any` writes."""
    return _MIME_BYTES if is_bytes_type else _MIME_JSON


def _durl_prefixes_for(is_bytes_type: bool) -> tuple[bytes, ...]:
    """Returns every Data URL header this library can emit for a value type.

    Used to recognise Cachetic's own format without claiming unrelated data
    URIs that a pre-0.7.0 ``bytes`` cache may have stored verbatim, so it has to
    stay exhaustive: a header :meth:`CacheticBase._dump_any` can write but this
    does not list reads back as legacy data and fails to validate.
    """
    mime: str = _mime_for(is_bytes_type)
    headers: list[str] = [f"data:{mime};base64,"]
    headers += [f"data:{mime};compression={name};base64," for name in _COMPRESSION_NAMES]
    return tuple(header.encode("utf-8") for header in headers)


def _detect_compression_name(data: bytes) -> str:
    """Returns compression algorithm name by inspecting magic bytes.

    The result is written into the Data URL, so it must stay within
    ``_COMPRESSION_NAMES`` — those are the headers the reader recognises.
    """
    from cachetic.utils.compression import ZSTD_MAGIC

    return _ZSTD if data.startswith(ZSTD_MAGIC) else _ZLIB


def _validate_ttl_value(ttl: int) -> int:
    """Validates and normalizes TTL values.

    Ensures TTL is either -1 (no expiration) or positive integer.
    """
    if ttl < 0:
        return -1
    return ttl
