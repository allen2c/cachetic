import subprocess
import sys
import textwrap

import pytest

from cachetic.extensions.compression import DecompressionError
from cachetic.utils.compression import (
    HAS_ZSTD,
    ZLIB_MAGIC,
    ZSTD_MAGIC,
    compress_auto,
    decompress_auto,
)


# Test Data Fixtures
@pytest.fixture
def sample_data():
    return b"Hello, Python! " * 50


@pytest.fixture
def tricky_raw_data():
    # Data that starts like zlib (0x78) but is actually garbage/uncompressed
    return b"\x78\x9c_not_really_compressed_data_"


# --- Test Zlib (Basic Functionality) ---


def test_zlib_roundtrip(sample_data):
    """Test that Zlib compression and decompression flow works correctly"""
    compressed = compress_auto(sample_data, method="zlib")
    restored = decompress_auto(compressed)

    assert compressed != sample_data, "Compressed data should differ from original"
    assert restored == sample_data, "Decompressed data should match original"
    assert compressed.startswith(b"\x78"), "Zlib data should start with 0x78"


# --- Test Zstd (Conditional Testing) ---


@pytest.mark.skipif(not HAS_ZSTD, reason="Zstandard library not installed, skipping test")
def test_zstd_roundtrip(sample_data):
    """Test Zstd compression and decompression flow (if installed)"""
    compressed = compress_auto(sample_data, method="zstd")
    restored = decompress_auto(compressed)

    assert restored == sample_data
    assert compressed.startswith(ZSTD_MAGIC), "Zstd data should contain Magic Header"


@pytest.mark.skipif(not HAS_ZSTD, reason="Zstandard library not installed, skipping test")
def test_zstd_is_safe_from_many_threads():
    """Concurrent zstd round-trips must not corrupt data or kill the process.

    zstandard's one-shot ``compress``/``decompress`` reuse an internal C context
    per object, so a module-level compressor shared between threads produces
    corrupted frames and segfaults the interpreter. Cachetic clients are meant
    to be shared, so this is ordinary traffic, not an exotic case.

    Run in a subprocess deliberately: the pre-fix failure mode is SIGSEGV, which
    would take the whole pytest process down instead of failing one test.
    """
    program = textwrap.dedent("""
        import os, threading
        from cachetic.utils.compression import compress_auto, decompress_auto

        errors = []

        def worker(index):
            try:
                for _ in range(300):
                    data = os.urandom(32) * (index + 1)
                    assert decompress_auto(compress_auto(data, method="zstd")) == data
            except BaseException as error:
                errors.append(f"{type(error).__name__}: {error}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors, errors[:3]
        """)
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, f"exit={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"


def test_auto_selection(sample_data):
    """Test whether 'auto' mode automatically selects the best option"""
    compressed = compress_auto(sample_data, method="auto")
    restored = decompress_auto(compressed)

    assert restored == sample_data

    if HAS_ZSTD:
        assert compressed.startswith(ZSTD_MAGIC), "Auto should prefer zstd when installed"
    else:
        assert compressed.startswith(ZLIB_MAGIC), "Auto should fall back to zlib when zstd not installed"


# --- Test Smart Detection Logic ---


def test_raw_data_passthrough(sample_data):
    """Test that plain uncompressed data can be returned as-is"""
    result = decompress_auto(sample_data)
    assert result == sample_data


def test_tricky_raw_data_passthrough(tricky_raw_data):
    """
    Test raw data that 'looks like Zlib' (starts with 0x78).
    Expected: decompression fails -> identified as raw -> returned as-is (no error).
    """
    result = decompress_auto(tricky_raw_data)
    assert result == tricky_raw_data


# --- Test Error Handling ---


def test_corrupt_zstd_raises_error():
    """
    Test corrupted Zstd data.
    Expected: header matches but content is broken -> raises DecompressionError.
    """
    if not HAS_ZSTD:
        pytest.skip("Zstandard must be installed to run this test")

    # Construct data with valid header but corrupted content
    corrupt_data = ZSTD_MAGIC + b"broken_content"

    with pytest.raises(DecompressionError) as excinfo:
        decompress_auto(corrupt_data)

    assert "Zstd decompression failed" in str(excinfo.value)


def test_missing_dependency_import_error():
    """Test behavior when zstd is required but not installed"""
    if HAS_ZSTD:
        pytest.skip("This test is only valid when Zstandard is not installed")

    with pytest.raises(ImportError):
        compress_auto(b"test", method="zstd")


def test_empty_input():
    """Boundary test: empty data"""
    assert compress_auto(b"") == b""
    assert decompress_auto(b"") == b""
    assert (
        decompress_auto(None) is None  # type: ignore
    )  # Depending on type checking strictness, None goes into 'not data' branch
