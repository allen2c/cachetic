"""Every backend answers the same adapter call the same way.

[Principle 2](../docs/PRINCIPLES.md) says every backend answers the one API
identically, and ``Cachetic.cache`` / ``await AsyncCachetic.cache()`` hand the
adapter to the caller, so the protocol is a public surface and not an internal
detail. The rest of the suite drives the backends through the client, which
normalises the TTL first — these tests go around it deliberately.

Before ``cachetic/extensions/_ttl.py`` existed, ``adapter.set(key, value, 0)``
went four ways at once: diskcache stored an already-expired value, redis raised
``invalid expire time in 'set' command``, and MongoDB and PostgreSQL stored it
forever. ``ex=-1`` — the default ``default_ttl`` — did exactly the same, and was
only ever saved by ``CacheticBase._ttl_to_expiry`` translating it a layer up.
"""

import pathlib
import uuid

import pydantic
import pytest

from cachetic import AsyncCachetic, Cachetic
from cachetic.aio import close_all as aio_close_all

STR_ADAPTER = pydantic.TypeAdapter(str)

NON_EXPIRING = [None, 0, -1]
"""Every ``ex`` that must mean "store this without a deadline"."""


def unique_key(label: str) -> str:
    return f"parity-{label}-{uuid.uuid4().hex}"


@pytest.fixture
def adapter(backend_url: str | pathlib.Path):
    return Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="parity").cache


@pytest.mark.parametrize("ex", NON_EXPIRING)
def test_sync_non_positive_ex_stores_without_expiry(adapter, ex: int | None):
    key = unique_key(f"sync-{ex}")
    try:
        adapter.set(key, b"kept", ex)

        assert adapter.get(key) == b"kept"
        assert adapter.exists(key) is True
    finally:
        adapter.delete(key)


@pytest.mark.parametrize("ex", NON_EXPIRING)
async def test_async_non_positive_ex_stores_without_expiry(backend_url: str | pathlib.Path, ex: int | None):
    key = unique_key(f"async-{ex}")
    cache = await AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="parity").cache()
    try:
        await cache.set(key, b"kept", ex)

        assert await cache.get(key) == b"kept"
        assert await cache.exists(key) is True
    finally:
        await cache.delete(key)
        await aio_close_all()


def test_sync_positive_ex_is_still_honoured(adapter):
    """The normalisation must not have flattened real TTLs into "never"."""
    key = unique_key("sync-positive")
    try:
        adapter.set(key, b"kept", 3600)
        assert adapter.get(key) == b"kept"
    finally:
        adapter.delete(key)


async def test_async_positive_ex_is_still_honoured(backend_url: str | pathlib.Path):
    key = unique_key("async-positive")
    cache = await AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="parity").cache()
    try:
        await cache.set(key, b"kept", 3600)
        assert await cache.get(key) == b"kept"
    finally:
        await cache.delete(key)
        await aio_close_all()
