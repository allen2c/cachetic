"""Reading the value format v0.7.0 writes.

The payloads below were produced by cachetic 0.7.0 and are pasted in verbatim
rather than generated, because that is the entire point: this release has to
read bytes it cannot itself produce, and a fixture built from local code would
pass even if v0.7.0's format were something else. Regenerate with:

    python -c "import pydantic; from cachetic._base import CacheticBase; ..."

on a 0.7.0 install, never from this branch.

A rolling update puts 0.6.x and 0.7.0 pods on one cache at the same time. Every
case here is a value a 0.7.0 pod writes and a 0.6.x pod then reads.
"""

import pydantic
import pytest

from cachetic import Cachetic


class Person(pydantic.BaseModel):
    name: str
    age: int


ALICE = Person(name="Alice", age=30)

# --- Written by cachetic 0.7.0 ----------------------------------------------
MODEL_PLAIN = b"data:application/json;base64,eyJuYW1lIjoiQWxpY2UiLCJhZ2UiOjMwfQ=="
MODEL_ZLIB = b"data:application/json;compression=zlib;base64,eJyrVspLzE1VslJyzMlMTlXSUUpMB/KMDWoBZLkHdA=="  # noqa: E501
MODEL_ZSTD = b"data:application/json;compression=zstd;base64,KLUv/SAZyQAAeyJuYW1lIjoiQWxpY2UiLCJhZ2UiOjMwfQ=="  # noqa: E501
STR_PLAIN = b"data:application/json;base64,ImhlbGxvIg=="
INT_PLAIN = b"data:application/json;base64,NDI="
NONE_PLAIN = b"data:application/json;base64,bnVsbA=="
BYTES_PLAIN = b"data:application/octet-stream;base64,AAFyYXc="
BYTES_ZLIB = b"data:application/octet-stream;compression=zlib;base64,eJxzdBweAADxaTLJ"
BYTES_ZSTD = b"data:application/octet-stream;compression=zstd;base64,KLUv/SDITQAAEEFBAQBDCmAB"  # noqa: E501


def _cache(type_, tmp_path, compression=False):
    return Cachetic(
        object_type=pydantic.TypeAdapter(type_),
        cache_url=str(tmp_path),
        compression=compression,
    )


@pytest.mark.parametrize(
    "type_, stored, expected",
    [
        (Person, MODEL_PLAIN, ALICE),
        (Person, MODEL_ZLIB, ALICE),
        (str, STR_PLAIN, "hello"),
        (int, INT_PLAIN, 42),
        (type(None), NONE_PLAIN, None),
        (bytes, BYTES_PLAIN, b"\x00\x01raw"),
        (bytes, BYTES_ZLIB, b"A" * 200),
    ],
)
@pytest.mark.parametrize("compression", [False, True])
def test_reads_v070_values(type_, stored, expected, compression, tmp_path):
    """Every v0.7.0 payload reads back, whatever `compression` is set to here.

    The flag decides what this client writes; it must not decide what it can
    read. A fleet mid-upgrade has both settings in it.
    """
    cache = _cache(type_, tmp_path, compression=compression)
    cache.cache.set("k", stored)

    assert cache.get("k") == expected


@pytest.mark.parametrize(
    "type_, stored, expected",
    [(Person, MODEL_ZSTD, ALICE), (bytes, BYTES_ZSTD, b"A" * 200)],
)
def test_reads_v070_zstd_values(type_, stored, expected, tmp_path):
    """zstd payloads need the extra; without it the read must raise, not guess."""
    from cachetic.utils.compression import HAS_ZSTD

    cache = _cache(type_, tmp_path)
    cache.cache.set("k", stored)

    if not HAS_ZSTD:
        # Notably including `bytes`, where returning the raw envelope would
        # validate cleanly and hand the caller silently wrong data.
        from cachetic.extensions.compression import DecompressionError

        with pytest.raises(DecompressionError):
            cache.get("k")
        return

    assert cache.get("k") == expected


def test_roundtrip_still_writes_the_legacy_format(tmp_path):
    """0.6.1 reads the new format but must never write it.

    This is what makes the 0.6.0 -> 0.6.1 rollout safe on its own: the 0.6.0
    pods still running alongside cannot read anything else.
    """
    cache = _cache(Person, tmp_path)
    cache.set("k", ALICE)

    raw = cache.cache.get("k")
    assert isinstance(raw, bytes)
    assert not raw.startswith(b"data:")
    assert raw == b'{"name":"Alice","age":30}'


def test_never_writes_zstd_even_when_available(tmp_path):
    """Compressed writes stay zlib whatever this image has installed.

    v0.6.0 uses zstd whenever `zstandard` imports, which makes the stored
    algorithm depend on the image rather than the data. Installing this
    release's `zstd` extra -- needed to read v0.7.0 -- would otherwise flip
    writes to zstd and lock out the 0.6.0 pods still being rolled over.
    """
    from cachetic.utils.compression import ZLIB_MAGIC, ZSTD_MAGIC

    cache = _cache(Person, tmp_path, compression=True)
    cache.set("k", ALICE)

    raw = cache.cache.get("k")
    assert isinstance(raw, bytes)
    assert not raw.startswith(ZSTD_MAGIC)
    assert raw.startswith(ZLIB_MAGIC)


def test_bytes_value_that_only_looks_like_a_data_url(tmp_path):
    """A `bytes` cache may legitimately hold something shaped like the envelope.

    Under the legacy format the payload is stored verbatim, so caching a data
    URI puts one of these in the cache with no v0.7.0 involved. The header
    matches and the base64 does not decode, so it has to come back untouched.
    """
    value = b"data:application/octet-stream;base64,not valid base64 !!"

    cache = _cache(bytes, tmp_path)
    cache.cache.set("k", value)

    assert cache.get("k") == value


def test_json_envelope_is_not_unwrapped_by_a_bytes_cache(tmp_path):
    """The MIME type is matched, not wildcarded.

    A `Cachetic[bytes]` holding a cached JSON data URI is legacy data that
    happens to look like a v0.7.0 JSON envelope. Unwrapping it would replace
    the caller's value with its own payload.
    """
    cache = _cache(bytes, tmp_path)
    cache.cache.set("k", MODEL_PLAIN)

    assert cache.get("k") == MODEL_PLAIN


def test_legacy_values_still_read(tmp_path):
    """The formats this release writes are unaffected by the new branch."""
    for compression in (False, True):
        cache = _cache(Person, tmp_path, compression=compression)
        cache.set(f"k{compression}", ALICE)
        assert cache.get(f"k{compression}") == ALICE
