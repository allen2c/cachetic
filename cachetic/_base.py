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
import warnings

import pydantic
import pydantic_settings

from cachetic.types.native_client import NativeCacheClient

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

    cache_url: str | pathlib.Path | NativeCacheClient
    cache_client: typing.Any = pydantic.Field(
        default=None,
        exclude=True,
        repr=False,
        description=(
            "A backend client the caller built and still owns. Not set directly: "
            "pass it as `cache_url`, the way v0.6.0 did."
        ),
    )
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
    _is_str_type: bool = pydantic.PrivateAttr(default=False)
    _durl_prefixes: tuple[bytes, ...] = pydantic.PrivateAttr(default=())

    @pydantic.model_validator(mode="before")
    @classmethod
    def accept_a_live_backend_client(cls, data: typing.Any) -> typing.Any:
        """Lets ``cache_url`` be a client the caller built, as it was in v0.6.0.

        v0.6.0 typed the field ``Text | Path | redis.Redis | diskcache.Cache``,
        so "I already configured a connection pool, use that one" was a
        supported way to construct a cache. [Principle 1](../docs/PRINCIPLES.md)
        does not let that call start failing validation.

        The object is moved to ``cache_client`` and ``cache_url`` is replaced
        with a label, because the URL is the connection registry's key and a
        caller-supplied client has no key: it was not opened here and
        :func:`cachetic.close_all` must not close it. The label is what shows up
        in logs, so it says what happened rather than pretending to be a URL.

        Recognition is by duck type, not ``isinstance``, so that this module
        stays free of ``import redis`` — [Principle 5](../docs/PRINCIPLES.md)
        requires no backend package be imported until its scheme is used, and a
        type annotation is enough to break that.
        """
        if not isinstance(data, dict):
            return data

        url = data.get("cache_url")
        if url is None or isinstance(url, (str, pathlib.Path)):
            return data

        # Anything that is not remotely a cache client falls through to pydantic
        # and is rejected there, as it was before this validator existed. Without
        # this, ``cache_url=12345`` would be quietly stored as a "client" and only
        # fail on the first cache operation, which can be a whole deployment away
        # from the line that misconfigured it.
        if not (hasattr(url, "get") and hasattr(url, "delete")):
            return data

        return {**data, "cache_client": url, "cache_url": _label_for_client(url)}

    @pydantic.model_validator(mode="after")
    def validate_after_init(self) -> typing.Self:
        """Validates and normalizes fields after model initialization."""
        if (
            isinstance(self.cache_url, str)
            and self.cache_url.startswith(_SUPPLIED_LABEL_MARK)
            and self.cache_client is None
        ):
            raise ValueError(
                f"cache_url is {self.cache_url!r}, the placeholder left behind when a "
                "backend client is supplied directly. The client itself is not part of "
                "`model_dump()` — it is a live connection, not configuration — so a model "
                "rebuilt from a dump has the label and nothing to use it with. Pass the "
                "client again, or configure this cache by URL so that it can be serialised."
            )

        self.default_ttl = _validate_ttl_value(self.default_ttl)
        self._is_bytes_type = inspect.isclass(self.object_type._type) and issubclass(self.object_type._type, bytes)
        # Exactly ``str``, not a subclass: this only exists to read the bare
        # UTF-8 v0.2.0 wrote for ``object_type=str``, and widening it would let
        # the fallback in _loads_legacy accept payloads for types that never had
        # that format. Enums and NewTypes over str are deliberately excluded.
        self._is_str_type = self.object_type._type is str
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
        then an envelope that does not decode falls back to the legacy path
        rather than surfacing as a base64 error on data that was never base64.

        That fallback covers the envelope and nothing else. Once the envelope
        has decoded, the value *is* this format, and a failure past that point
        is reported rather than retried — see :meth:`_loads_durl_payload`.
        """
        # Path 1: Data URL format (v0.7.0+)
        if data.startswith(self._durl_prefixes):
            try:
                payload, compression_alg = _decode_durl(data)
            except ValueError as durl_error:
                logger.debug(
                    "Value looks like a Cachetic Data URL but did not decode, "
                    "falling back to the pre-0.7.0 format. Error: %s",
                    durl_error,
                )
            else:
                return self._loads_durl_payload(payload, compression_alg)

        # Path 2 & 3: Legacy formats (compressed or raw)
        return self._loads_legacy(data)

    def _loads_durl_payload(self, payload: bytes, compression_alg: str | None) -> T:
        """Decompresses and validates a decoded Data URL payload.

        Deliberately outside the legacy fallback in :meth:`_loads_any`. Both
        steps here raise ``ValueError`` subclasses — ``DecompressionError`` and
        ``pydantic.ValidationError`` — so retrying them as legacy data would
        conflate "this was never the new format" with "this is the new format
        and I cannot read it".

        The second one is not hypothetical. A reader without ``zstandard``
        cannot decompress what a writer that had it produced, and every byte
        string validates as ``bytes``: falling back would hand a
        ``Cachetic[bytes]`` the undecoded Data URL as its value, silently,
        turning a missing dependency into wrong data. ``Cachetic[str]`` reaches
        the same end through :meth:`_loads_bare_str`. Both now raise.
        """
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

            bare = self._loads_bare_str(data)
            if bare is not None:
                return bare

            logger.error(f"Validation error: {e!s}")
            raise

    def _loads_bare_str(self, data: bytes) -> T | None:
        """Reads the unquoted UTF-8 that v0.2.0 wrote for ``object_type=str``.

        v0.2.0 stored a ``str`` as its raw bytes; v0.3.0 switched to JSON, which
        quotes it. :meth:`_validate_any` calls ``validate_json``, so ``b"hello"``
        has been unreadable ever since — [Principle 1](../docs/PRINCIPLES.md)
        puts the data floor at v0.1.0, and this was under it.

        Only reached after ``validate_json`` has already failed, so a value
        written by v0.3.0 or later still parses as JSON and never gets here. That
        ordering leaves one case that cannot be recovered and never could: a
        v0.2.0 string whose own text is valid JSON. ``b'"quoted"'`` parses as the
        string ``quoted`` and loses its quotes, because nothing in the format
        distinguishes it from what v0.3.0 would have written for ``quoted``.

        Returns ``None`` when this is not that format, so the caller can raise
        the original validation error rather than one from here.

        **This costs a `Cachetic[str]` its loud failure on corruption, and there
        is no version of it that does not.** v0.2.0's format was "the bytes of
        the string", so every byte string is a valid value under it — a truncated
        write, a torn read or a corrupt page is indistinguishable from a string
        that happens to look like one. ``Cachetic[int]`` still raises on the same
        input; ``Cachetic[str]`` cannot.

        So it warns. The read succeeds, because
        [Principle 1](../docs/PRINCIPLES.md) says a value an earlier version
        wrote must come back, but it does not pass in silence: anything reaching
        here was written before v0.3.0, which is five releases of cache expiry
        ago, and is far more likely to be damage than history.
        """
        if not self._is_str_type:
            return None
        try:
            text: str = data.decode("utf-8")
        except UnicodeDecodeError:
            return None

        logger.warning(
            "Read %d bytes that are not valid JSON as a bare string. This is the "
            "pre-v0.3.0 format for object_type=str and is returned as-is. If this "
            "cache was not written by v0.2.x, the value is corrupt: %.80r",
            len(data),
            data,
        )
        return self.object_type.validate_python(text)

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


def _decode_durl(data: bytes) -> tuple[bytes, str | None]:
    """Decodes a Data URL envelope into its payload and compression parameter.

    Split out of the read path so that exactly one step can say "this was never
    Cachetic's format": a ``ValueError`` from here — a header ``durl`` rejects,
    a payload that is not base64, bytes that are not UTF-8 — is the caller's
    signal to fall back to the pre-0.7.0 path. Nothing downstream of it gets
    that treatment, because by then the value has identified itself.
    """
    from durl import DURL

    durl: DURL = DURL(data.decode("utf-8"))
    payload: bytes = typing.cast(bytes, durl.parsed_data)
    return payload, durl.parameters.get("compression")


def _detect_compression_name(data: bytes) -> str:
    """Returns compression algorithm name by inspecting magic bytes.

    The result is written into the Data URL, so it must stay within
    ``_COMPRESSION_NAMES`` — those are the headers the reader recognises.
    """
    from cachetic.utils.compression import ZSTD_MAGIC

    return _ZSTD if data.startswith(ZSTD_MAGIC) else _ZLIB


def driver_modules(client: typing.Any) -> frozenset[str]:
    """Every module the client's class and its bases came from.

    Routing a supplied client cannot use ``isinstance``: naming ``redis.Redis``
    means importing it, and [Principle 5](../docs/PRINCIPLES.md) says no backend
    package is imported until its scheme is used. So it goes by module name.

    The whole MRO, not just ``type(client).__module__``, because a subclass
    defined in application code reports *that* module — ``myapp.cache.TracedRedis``
    is a ``redis.Redis`` and has to route like one. Wrapping a client in a
    subclass to add instrumentation is exactly what someone who builds their own
    client is likely to have done.
    """
    return frozenset(base.__module__ for base in type(client).__mro__)


def is_async_redis(modules: frozenset[str]) -> bool:
    """True for ``redis.asyncio`` clients, which both halves must route apart.

    Sync and async share the ``redis`` top level, and confusing them fails
    quietly rather than loudly: a sync adapter calling an async client's ``set``
    gets a coroutine back, never awaits it, and reports success on a write that
    never happened.
    """
    return any(module.startswith("redis.asyncio") for module in modules)


_SUPPLIED_LABEL_MARK = "<cachetic supplied client:"
"""Opening of the placeholder, and the way to recognise one that lost its client.

A label reaching the backend router would fall through every scheme test to the
disk default and quietly build a cache in a directory named after it. So it is
recognisable, and `validate_after_init` refuses a model that has the label
without the client. See :func:`_label_for_client`.
"""


def _label_for_client(client: typing.Any) -> str:
    """Names a caller-supplied client where a URL would otherwise go.

    Deliberately not URL-shaped: nothing may parse this back into a scheme and
    route on it, and it must never collide with a real registry key. It carries
    no repr of the client, so a connection string inside one cannot leak through
    ``cache_url_safe``.
    """
    return f"{_SUPPLIED_LABEL_MARK} {type(client).__module__}.{type(client).__qualname__}>"


def warn_ignored_arguments(method: str, args: tuple[typing.Any, ...], kwargs: dict[str, typing.Any]) -> None:
    """Accepts the extra arguments v0.6.0 swallowed, and says so.

    [Principle 1](../docs/PRINCIPLES.md) requires every call v0.6.0 accepted to
    keep being accepted. v0.6.0 declared ``*args, **kwargs`` on ``get``,
    ``get_or_raise``, ``set`` and ``delete`` and ignored whatever landed there,
    so ``cache.delete("k", conn)`` ran. Dropping the two from the signature made
    that same call a ``TypeError`` — a rejection, not a different answer, which
    is the half of rule 1 that has no "behaviour is out of scope" escape.

    They are still ignored. What is new is that ignoring them is now audible:
    the call that reaches here is either a leftover from an older Cachetic or a
    typo, and neither should stay silent for another release.

    ``get``'s ``default`` is the one extra argument that is *not* ignored — it
    became a real parameter in 0.7.0, so ``cache.get("k", "fallback")`` finally
    does what it always read as.
    """
    if not args and not kwargs:
        return

    ignored: list[str] = [repr(value) for value in args]
    ignored += [f"{name}={value!r}" for name, value in kwargs.items()]
    warnings.warn(
        f"Cachetic.{method}() ignores {', '.join(ignored)}. "
        f"Extra arguments were accepted and discarded before v0.7.0; they are still "
        f"discarded, and passing them will become a TypeError in a future release.",
        DeprecationWarning,
        stacklevel=3,
    )


def _validate_ttl_value(ttl: int) -> int:
    """Validates and normalizes TTL values.

    Ensures TTL is either -1 (no expiration) or positive integer.
    """
    if ttl < 0:
        return -1
    return ttl
