import logging
import zlib
from typing import Literal

from cachetic.extensions.compression import DecompressionError

logger = logging.getLogger(__name__)

# Try to import zstandard (pip install zstandard)
# This prepares the code for future Python versions or environments with zstd support.
try:
    import zstandard as zstd

    HAS_ZSTD = True
    logger.debug("Zstandard library found")
except ImportError:
    HAS_ZSTD = False
    logger.debug("Zstandard library not found")


# Zstd frame magic header (Little Endian: 0xFD2FB528 -> Bytes: 28 B5 2F FD)
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
# Zlib default header usually starts with 0x78 (Deflate)
ZLIB_MAGIC = b"\x78"


def might_compressed(data: bytes) -> bool:
    """
    Checks if the data might be compressed.
    """
    return data.startswith(ZSTD_MAGIC) or data.startswith(ZLIB_MAGIC)


def compress_auto(
    data: bytes, method: Literal["auto", "zstd", "zlib"] = "auto"
) -> bytes:
    """
    Compresses data using the best available algorithm or the specified method.

    Args:
        data: The raw bytes to compress.
        method: 'auto', 'zstd', or 'zlib'.
                - 'auto': Prefers zstd if available, otherwise falls back to zlib.
                - 'zstd': Forces zstd compression (raises ImportError if unavailable).
                - 'zlib': Forces zlib compression.

    Returns:
        The compressed bytes.
    """
    if not data:
        return b""

    # Determine strategy
    use_zstd = False
    if method == "zstd":
        if not HAS_ZSTD:
            raise ImportError(
                "Method 'zstd' requested but zstandard library is not installed."
            )
        use_zstd = True
    elif method == "auto":
        use_zstd = HAS_ZSTD  # Use zstd if we have it, else zlib

    # Execute compression
    if use_zstd:
        cctx = zstd.ZstdCompressor(level=3)  # level 3 is default balance
        return cctx.compress(data)
    else:
        # level=6 is zlib default balance
        return zlib.compress(data, level=6)


def decompress_auto(data: bytes) -> bytes:
    """
    Automatically detects format (Zstd, Zlib, or Raw) and returns raw bytes.

    Logic:
    1. Checks for Zstd magic bytes. If found, tries Zstd decompression.
       - If fails: Raises DecompressionError (Zstd header implies strict data).
    2. Checks for Zlib magic bytes. If found, tries Zlib decompression.
       - If fails: Returns original data (Assumes collision with raw data starting with 0x78).
    3. If no match, returns original data (Raw).

    Args:
        data: The bytes to decompress.

    Returns:
        The raw (decompressed) bytes.

    Raises:
        DecompressionError: If a valid Zstd header is found but data is corrupted,
                            or if zstd data is found but the library is missing.
    """  # noqa: E501
    if not data or len(data) < 2:
        return data

    # 1. Zstandard Detection (High Confidence)
    if data.startswith(ZSTD_MAGIC):
        if not HAS_ZSTD:
            # We identified it's Zstd, but we can't process it.
            # This is a system configuration error, not a data error.
            raise DecompressionError(
                "Detected Zstd data but 'zstandard' library is not installed."
            )

        try:
            dctx = zstd.ZstdDecompressor()
            return dctx.decompress(data)
        except Exception as e:
            logger.error(f"Detect Zstd header but decompression failed: {str(e)}")
            raise DecompressionError(f"Zstd decompression failed: {str(e)}") from e

    # 2. Zlib Detection (Medium Confidence)
    if data.startswith(ZLIB_MAGIC):
        try:
            return zlib.decompress(data)
        except zlib.error:
            logger.warning(
                "Detect Zlib header but decompression failed, "
                + "it might be raw data starting with 0x78."
            )
            return data

    # 3. Raw Data
    return data
