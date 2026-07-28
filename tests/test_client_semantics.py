"""Miss semantics, the ``default`` argument, and the disabled client.

Every test runs against both clients so the sync and async halves cannot drift,
and against every backend via the ``backend_url`` fixture — the behaviour under
test is decided in ``cachetic/_base.py`` and the two client classes, but a
backend that reported a miss differently would break it just as surely.
"""

import pathlib
import uuid

import pydantic
import pytest

from cachetic import AsyncCachetic, CacheNotFoundError, Cachetic
from cachetic.aio import close_all as aio_close_all

STR_ADAPTER = pydantic.TypeAdapter(str)
OPTIONAL_ADAPTER: pydantic.TypeAdapter[str | None] = pydantic.TypeAdapter(str | None)


def unique_key(label: str) -> str:
    """Backends are shared between tests and runs, so keys must not repeat."""
    return f"{label}-{uuid.uuid4().hex}"


@pytest.fixture
def sync_cache(backend_url: str | pathlib.Path):
    return Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="semantics")


@pytest.fixture
async def async_cache(backend_url: str | pathlib.Path):
    cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="semantics")
    try:
        yield cache
    finally:
        await aio_close_all()


class TestDefaultArgument:
    """``get`` takes an explicit ``default``, replacing a silent ``**kwargs``.

    Before 0.7.0 the four operations accepted ``*args, **kwargs`` and ignored
    them, so ``cache.get("k", "fallback")`` — the ``dict.get`` habit — returned
    None and threw the fallback away without a word.
    """

    def test_sync_miss_returns_the_default(self, sync_cache: Cachetic[str]):
        assert sync_cache.get(unique_key("missing"), "fallback") == "fallback"

    async def test_async_miss_returns_the_default(self, async_cache: AsyncCachetic[str]):
        assert await async_cache.get(unique_key("missing"), "fallback") == "fallback"

    def test_sync_hit_ignores_the_default(self, sync_cache: Cachetic[str]):
        key = unique_key("hit")
        sync_cache.set(key, "stored")
        try:
            assert sync_cache.get(key, "fallback") == "stored"
        finally:
            sync_cache.delete(key)

    async def test_async_hit_ignores_the_default(self, async_cache: AsyncCachetic[str]):
        key = unique_key("hit")
        await async_cache.set(key, "stored")
        try:
            assert await async_cache.get(key, "fallback") == "stored"
        finally:
            await async_cache.delete(key)

    def test_unknown_keyword_now_raises(self, sync_cache: Cachetic[str]):
        """The old signature swallowed anything; a typo has to be an error."""
        with pytest.raises(TypeError):
            sync_cache.get(unique_key("typo"), defualt="fallback")  # type: ignore[call-arg]


class TestStoredNoneIsAHit:
    """A cache of an optional type can store ``None``, and that is not a miss.

    ``get`` used to collapse the two, so ``get_or_raise`` raised on a key that
    ``exists`` reported as present.
    """

    def test_sync_stored_none_is_not_the_default(self, backend_url: str | pathlib.Path):
        cache = Cachetic[str | None](object_type=OPTIONAL_ADAPTER, cache_url=backend_url, prefix="semantics")
        key = unique_key("null")
        cache.set(key, None)
        try:
            assert cache.exists(key) is True
            assert cache.get(key, "fallback") is None
        finally:
            cache.delete(key)

    def test_sync_get_or_raise_does_not_raise_on_stored_none(self, backend_url: str | pathlib.Path):
        cache = Cachetic[str | None](object_type=OPTIONAL_ADAPTER, cache_url=backend_url, prefix="semantics")
        key = unique_key("null")
        cache.set(key, None)
        try:
            assert cache.get_or_raise(key) is None
        finally:
            cache.delete(key)

    async def test_async_get_or_raise_does_not_raise_on_stored_none(self, backend_url: str | pathlib.Path):
        cache = AsyncCachetic[str | None](object_type=OPTIONAL_ADAPTER, cache_url=backend_url, prefix="semantics")
        key = unique_key("null")
        await cache.set(key, None)
        try:
            assert await cache.get_or_raise(key) is None
        finally:
            await cache.delete(key)
            await aio_close_all()

    def test_sync_get_or_raise_still_raises_on_a_real_miss(self, sync_cache: Cachetic[str]):
        with pytest.raises(CacheNotFoundError):
            sync_cache.get_or_raise(unique_key("missing"))

    async def test_async_get_or_raise_still_raises_on_a_real_miss(self, async_cache: AsyncCachetic[str]):
        with pytest.raises(CacheNotFoundError):
            await async_cache.get_or_raise(unique_key("missing"))


class TestDisabledClient:
    """``default_ttl=0`` turns a client off: reads miss, writes are dropped.

    Previously it only dropped writes, so a client configured to disable caching
    kept serving whatever an earlier client had written — the opposite of what
    "disable" promises, and invisible because ``set`` returned normally.
    """

    def test_sync_disabled_client_does_not_serve_existing_data(self, backend_url: str | pathlib.Path):
        key = unique_key("disabled")
        writer = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="semantics", default_ttl=3600)
        disabled = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="semantics", default_ttl=0)

        writer.set(key, "written-before-disabling")
        try:
            disabled.set(key, "never-stored")

            assert disabled.get(key) is None
            assert disabled.get(key, "fallback") == "fallback"
            assert disabled.exists(key) is False
            with pytest.raises(CacheNotFoundError):
                disabled.get_or_raise(key)

            # The value is untouched, not evicted — another client still reads it.
            assert writer.get(key) == "written-before-disabling"
        finally:
            writer.delete(key)

    async def test_async_disabled_client_does_not_serve_existing_data(self, backend_url: str | pathlib.Path):
        key = unique_key("disabled")
        writer = AsyncCachetic[str](
            object_type=STR_ADAPTER, cache_url=backend_url, prefix="semantics", default_ttl=3600
        )
        disabled = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="semantics", default_ttl=0)

        await writer.set(key, "written-before-disabling")
        try:
            await disabled.set(key, "never-stored")

            assert await disabled.get(key) is None
            assert await disabled.exists(key) is False
            with pytest.raises(CacheNotFoundError):
                await disabled.get_or_raise(key)

            assert await writer.get(key) == "written-before-disabling"
        finally:
            await writer.delete(key)
            await aio_close_all()

    def test_disabled_client_can_still_delete(self, backend_url: str | pathlib.Path):
        """Removing a value must not depend on whether this client would write it."""
        key = unique_key("disabled-delete")
        writer = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="semantics", default_ttl=3600)
        disabled = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="semantics", default_ttl=0)

        writer.set(key, "value")
        disabled.delete(key)

        assert writer.get(key) is None

    def test_per_call_zero_ttl_drops_the_write_without_evicting(self, sync_cache: Cachetic[str]):
        """``ex=0`` means "do not cache this value", not "remove that key".

        Distinct from a disabled client: the caller is skipping one write, not
        turning the cache off, so an existing entry stays readable.
        """
        key = unique_key("ex-zero")
        sync_cache.set(key, "kept")
        try:
            sync_cache.set(key, "not-stored", ex=0)
            assert sync_cache.get(key) == "kept"
        finally:
            sync_cache.delete(key)
