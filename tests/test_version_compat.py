"""Version compatibility tests for Data URL and legacy storage formats.

Verifies all 8 read-path combinations in _loads_any and _dump_any output format.
Legacy data is injected directly via cache.cache.set() to simulate pre-v0.7.0 storage.
"""

import pathlib
import zlib

import pydantic
import pytest
from durl import DURL

from cachetic import Cachetic
from cachetic.utils.compression import HAS_ZSTD, compress_auto

if HAS_ZSTD:
    import zstandard as zstd


class Person(pydantic.BaseModel):
    name: str
    age: int


PERSON: Person = Person(name="Alice", age=30)
PERSON_ADAPTER: pydantic.TypeAdapter[Person] = pydantic.TypeAdapter(Person)
PERSON_JSON: bytes = PERSON_ADAPTER.dump_json(PERSON)
BYTES_ADAPTER: pydantic.TypeAdapter[bytes] = pydantic.TypeAdapter(bytes)
RAW_BYTES: bytes = b"\x00\x01\x02\x03\xff"


# ===== Path 1: Data URL format (v0.7.0 current) =====


class TestDurlReadPath:
    """Verifies _loads_any Path 1: data: prefix → DURL parsing."""

    def test_durl_uncompressed_json(self, temp_cache_url: pathlib.Path) -> None:
        """DURL application/json without compression."""
        cache: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
        )
        durl_bytes: bytes = str(
            DURL.build(mime_type="application/json", data=PERSON_JSON)
        ).encode("utf-8")
        cache.cache.set("k", durl_bytes)
        assert cache.get("k") == PERSON

    @pytest.mark.skipif(not HAS_ZSTD, reason="zstandard not installed")
    def test_durl_compression_zstd(self, temp_cache_url: pathlib.Path) -> None:
        """DURL application/json with compression=zstd parameter."""
        cache: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
        )
        compressed: bytes = compress_auto(PERSON_JSON, method="zstd")
        durl_bytes: bytes = str(
            DURL.build(
                mime_type="application/json",
                data=compressed,
                parameters={"compression": "zstd"},
            )
        ).encode("utf-8")
        cache.cache.set("k", durl_bytes)
        assert cache.get("k") == PERSON

    def test_durl_compression_zlib(self, temp_cache_url: pathlib.Path) -> None:
        """DURL application/json with compression=zlib parameter."""
        cache: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
        )
        compressed: bytes = compress_auto(PERSON_JSON, method="zlib")
        durl_bytes: bytes = str(
            DURL.build(
                mime_type="application/json",
                data=compressed,
                parameters={"compression": "zlib"},
            )
        ).encode("utf-8")
        cache.cache.set("k", durl_bytes)
        assert cache.get("k") == PERSON

    def test_durl_bytes_octet_stream(self, temp_cache_url: pathlib.Path) -> None:
        """DURL application/octet-stream for bytes type."""
        cache: Cachetic[bytes] = Cachetic[bytes](
            object_type=BYTES_ADAPTER,
            cache_url=temp_cache_url,
        )
        durl_bytes: bytes = str(
            DURL.build(mime_type="application/octet-stream", data=RAW_BYTES)
        ).encode("utf-8")
        cache.cache.set("k", durl_bytes)
        assert cache.get("k") == RAW_BYTES


# ===== Path 2 & 3: Legacy formats (pre-v0.7.0 data) =====


class TestLegacyReadPath:
    """Verifies _loads_any Path 2 (compressed) and Path 3 (raw) for legacy data."""

    def test_legacy_raw_json(self, temp_cache_url: pathlib.Path) -> None:
        """Plain JSON bytes stored by v0.1.0–v0.4.x (no compression)."""
        cache: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
        )
        cache.cache.set("k", PERSON_JSON)
        assert cache.get("k") == PERSON

    @pytest.mark.skipif(not HAS_ZSTD, reason="zstandard not installed")
    def test_legacy_zstd_compressed(self, temp_cache_url: pathlib.Path) -> None:
        """Zstd-compressed JSON bytes stored by v0.5.x with compression=True."""
        cache: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
            compression=True,
        )
        compressed: bytes = zstd.ZstdCompressor().compress(PERSON_JSON)
        cache.cache.set("k", compressed)
        assert cache.get("k") == PERSON

    def test_legacy_zlib_compressed(self, temp_cache_url: pathlib.Path) -> None:
        """Zlib-compressed JSON bytes stored by v0.5.x with compression=True."""
        cache: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
            compression=True,
        )
        compressed: bytes = zlib.compress(PERSON_JSON)
        cache.cache.set("k", compressed)
        assert cache.get("k") == PERSON

    def test_legacy_raw_bytes(self, temp_cache_url: pathlib.Path) -> None:
        """Raw bytes stored by v0.1.0+ for bytes type."""
        cache: Cachetic[bytes] = Cachetic[bytes](
            object_type=BYTES_ADAPTER,
            cache_url=temp_cache_url,
        )
        cache.cache.set("k", RAW_BYTES)
        assert cache.get("k") == RAW_BYTES

    def test_legacy_compressed_bytes_with_matching_flag(
        self,
        temp_cache_url: pathlib.Path,
    ) -> None:
        """Compressed raw bytes stored by v0.5.x with compression=True.

        Pre-0.7.0 values carry no algorithm marker, and every byte string is a
        valid ``bytes``, so there is no validation error to recover from: the
        reader has to be configured the way the writer was. Values written from
        0.7.0 on are self-describing and lift this requirement.
        """
        cache: Cachetic[bytes] = Cachetic[bytes](
            object_type=BYTES_ADAPTER,
            cache_url=temp_cache_url,
            compression=True,
        )
        cache.cache.set("k", zlib.compress(RAW_BYTES))
        assert cache.get("k") == RAW_BYTES


class TestLegacyBytesThatLookLikeDataUrls:
    """Pre-0.7.0 ``bytes`` values may themselves begin with ``data:``.

    A bytes cache stores its payload verbatim, so caching a data URI produces
    stored data indistinguishable from a format marker unless detection is
    narrow. These pin that such values stay readable.
    """

    def test_foreign_mime_is_read_as_legacy(
        self,
        temp_cache_url: pathlib.Path,
    ) -> None:
        """A valid data URI Cachetic never emits must come back verbatim."""
        cache: Cachetic[bytes] = Cachetic[bytes](
            object_type=BYTES_ADAPTER,
            cache_url=temp_cache_url,
        )
        stored: bytes = b"data:image/png;base64,SEVMTE8="
        cache.cache.set("k", stored)
        assert cache.get("k") == stored

    def test_unparseable_data_uri_is_read_as_legacy(
        self,
        temp_cache_url: pathlib.Path,
    ) -> None:
        """Something that only looks like a data URI must not raise."""
        cache: Cachetic[bytes] = Cachetic[bytes](
            object_type=BYTES_ADAPTER,
            cache_url=temp_cache_url,
        )
        stored: bytes = b"data:image/png;base64,not_really_base64!!"
        cache.cache.set("k", stored)
        assert cache.get("k") == stored

    def test_own_header_but_corrupt_payload_falls_back(
        self,
        temp_cache_url: pathlib.Path,
    ) -> None:
        """Cachetic's own header with an unusable payload still reads as legacy."""
        cache: Cachetic[bytes] = Cachetic[bytes](
            object_type=BYTES_ADAPTER,
            cache_url=temp_cache_url,
        )
        stored: bytes = b"data:application/octet-stream;base64,!!!not base64!!!"
        cache.cache.set("k", stored)
        assert cache.get("k") == stored

    def test_json_cache_ignores_octet_stream_header(
        self,
        temp_cache_url: pathlib.Path,
    ) -> None:
        """Detection is per value type: a JSON cache must not claim bytes headers."""
        cache: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
        )
        cache.cache.set("k", b"data:application/octet-stream;base64,SEVMTE8=")
        with pytest.raises(pydantic.ValidationError):
            cache.get("k")

    def test_roundtrip_of_a_data_uri_payload(
        self,
        temp_cache_url: pathlib.Path,
    ) -> None:
        """Writing a data URI through the public API round-trips unchanged."""
        cache: Cachetic[bytes] = Cachetic[bytes](
            object_type=BYTES_ADAPTER,
            cache_url=temp_cache_url,
        )
        value: bytes = b"data:application/octet-stream;base64,SEVMTE8="
        cache.set("k", value)
        assert cache.get("k") == value


# ===== _dump_any output format assertions =====


class TestDumpAnyFormat:
    """Verifies _dump_any produces well-formed Data URL strings."""

    def test_json_uncompressed_prefix(self, temp_cache_url: pathlib.Path) -> None:
        """Uncompressed JSON → starts with data:application/json;base64,"""
        cache: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
            compression=False,
        )
        text: str = cache._dump_any(PERSON).decode("utf-8")
        assert text.startswith("data:application/json;base64,")

    def test_octet_stream_prefix(self, temp_cache_url: pathlib.Path) -> None:
        """Bytes type → starts with data:application/octet-stream;base64,"""
        cache: Cachetic[bytes] = Cachetic[bytes](
            object_type=BYTES_ADAPTER,
            cache_url=temp_cache_url,
            compression=False,
        )
        text: str = cache._dump_any(RAW_BYTES).decode("utf-8")
        assert text.startswith("data:application/octet-stream;base64,")

    @pytest.mark.skipif(not HAS_ZSTD, reason="zstandard not installed")
    def test_compressed_has_compression_param(
        self,
        temp_cache_url: pathlib.Path,
    ) -> None:
        """With compression=True, DURL contains a compression= parameter."""
        cache: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
            compression=True,
        )
        text: str = cache._dump_any(PERSON).decode("utf-8")
        assert "compression=zstd" in text or "compression=zlib" in text

    def test_roundtrip_uncompressed(self, temp_cache_url: pathlib.Path) -> None:
        """_dump_any ↔ _loads_any round-trip without compression."""
        cache: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
            compression=False,
        )
        assert cache._loads_any(cache._dump_any(PERSON)) == PERSON

    def test_roundtrip_compressed(self, temp_cache_url: pathlib.Path) -> None:
        """_dump_any ↔ _loads_any round-trip with compression."""
        cache: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
            compression=True,
        )
        assert cache._loads_any(cache._dump_any(PERSON)) == PERSON


# ===== Cross-configuration reads (DURL self-describing) =====


class TestCrossConfigRead:
    """Verifies that DURL's self-describing format allows cross-config reads.

    A compressed writer's output can be read by an uncompressed reader
    (and vice versa) because compression info lives in the DURL parameter,
    not in the instance's compression flag.
    """

    @pytest.mark.skipif(not HAS_ZSTD, reason="zstandard not installed")
    def test_compressed_writer_uncompressed_reader(
        self,
        temp_cache_url: pathlib.Path,
    ) -> None:
        """Writer compression=True, reader compression=False."""
        writer: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
            compression=True,
        )
        reader: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
            compression=False,
        )
        writer.set("cross", PERSON)
        assert reader.get("cross") == PERSON

    def test_uncompressed_writer_compressed_reader(
        self,
        temp_cache_url: pathlib.Path,
    ) -> None:
        """Writer compression=False, reader compression=True."""
        writer: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
            compression=False,
        )
        reader: Cachetic[Person] = Cachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=temp_cache_url,
            compression=True,
        )
        writer.set("cross", PERSON)
        assert reader.get("cross") == PERSON
