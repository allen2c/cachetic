"""``cache_url`` accepts a backend client the caller already built.

v0.6.0 typed the field ``Text | Path | redis.Redis | diskcache.Cache``, so
"I configured this connection pool, use it" was a supported way to construct a
cache. v0.7.0 narrowed it to ``str | pathlib.Path`` and that call started failing
pydantic validation — a rejection, which is the half of
[Principle 1](../docs/PRINCIPLES.md) with no escape.

Two things follow from the client being the caller's, and both are tested here:

* it is never entered into the connection registry, because the registry keys on
  a URL and this has none;
* `close_all()` must leave it open. Closing a pool the library did not open turns
  a shared client into a dead one for whoever else is holding it.

What is *not* restored is `.cache` handing the driver back. That is the adapter,
and rule 1 names it as out of scope — see `tests/test_v060_api_floor.py`.
"""

import pathlib

import diskcache
import pydantic
import pytest
import redis
import redis.asyncio

import cachetic
from cachetic import AsyncCachetic, Cachetic
from cachetic.aio import close_all as aio_close_all

from .conftest import require_service

STR_ADAPTER = pydantic.TypeAdapter(str)


@pytest.fixture
def live_disk_cache(tmp_path: pathlib.Path):
    raw = diskcache.Cache(str(tmp_path.joinpath("owned-by-caller")))
    try:
        yield raw
    finally:
        raw.close()


class TestDiskClient:
    def test_sync_uses_the_supplied_cache(self, live_disk_cache: diskcache.Cache):
        cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=live_disk_cache)
        cache.set("k", "value")

        assert cache.get("k") == "value"
        # Straight out of the caller's own object, not through Cachetic.
        assert live_disk_cache.get("k") is not None

    async def test_async_uses_the_supplied_cache(self, live_disk_cache: diskcache.Cache):
        """``diskcache`` has no async API and no loop affinity, so both take it."""
        cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=live_disk_cache)
        try:
            await cache.set("k", "value")
            assert await cache.get("k") == "value"
        finally:
            await aio_close_all()

    def test_close_all_leaves_it_open(self, live_disk_cache: diskcache.Cache):
        """The library did not open it, so the library does not close it."""
        cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=live_disk_cache)
        cache.set("k", "value")

        cachetic.close_all()

        assert live_disk_cache.get("k") is not None, "close_all() closed a caller-owned handle"
        assert cache.get("k") == "value"

    def test_it_is_not_in_the_registry(self, live_disk_cache: diskcache.Cache):
        """No URL, no key — nothing to share it under and nothing to evict."""
        from cachetic.extensions import _registry

        before = _registry._registry_size()
        Cachetic[str](object_type=STR_ADAPTER, cache_url=live_disk_cache).set("k", "v")

        assert _registry._registry_size() == before


class TestRedisClient:
    def test_sync_uses_the_supplied_client(self, redis_connection_string: str):
        raw = redis.Redis.from_url(redis_connection_string)
        try:
            cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=raw, prefix="supplied")
            cache.set("k", "value")
            try:
                assert cache.get("k") == "value"
                assert raw.get("supplied:k") is not None
            finally:
                cache.delete("k")
        finally:
            raw.close()

    def test_close_all_leaves_the_pool_open(self, redis_connection_string: str):
        raw = redis.Redis.from_url(redis_connection_string)
        try:
            cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=raw, prefix="supplied")
            cache.set("k", "value")

            cachetic.close_all()

            assert raw.ping() is True, "close_all() closed a caller-owned connection pool"
            assert cache.get("k") == "value"
            cache.delete("k")
        finally:
            raw.close()

    async def test_async_takes_the_asyncio_client(self):
        url = require_service("redis://localhost:6379/0", label="Redis")
        raw = redis.asyncio.Redis.from_url(url)
        try:
            cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=raw, prefix="supplied")
            await cache.set("k", "value")
            try:
                assert await cache.get("k") == "value"
            finally:
                await cache.delete("k")
                await aio_close_all()
        finally:
            await raw.aclose()

    async def test_async_refuses_a_blocking_client(self, redis_connection_string: str):
        """A sync client in the async path would block the loop on every call."""
        raw = redis.Redis.from_url(redis_connection_string)
        try:
            cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=raw)
            with pytest.raises(TypeError, match=r"redis\.asyncio"):
                await cache.get("k")
        finally:
            raw.close()


class TestRoutingBySomethingOtherThanIsinstance:
    """Dispatch reads module names, because naming the classes would import them.

    ``_base.py`` may not ``import redis`` — [Principle 5](../docs/PRINCIPLES.md).
    So routing goes by where the class and its bases came from, and these are the
    two ways that gets it wrong if it is done carelessly.
    """

    def test_a_user_subclass_of_redis_still_routes_to_redis(self, redis_connection_string: str):
        """``type(client).__module__`` would say this module, not ``redis``.

        Wrapping a client in a subclass to add tracing or metrics is exactly what
        someone who builds their own client is likely to have done, so the whole
        MRO is what gets looked at.
        """

        class TracedRedis(redis.Redis):  # type: ignore[type-arg]
            pass

        raw = TracedRedis.from_url(redis_connection_string)
        try:
            cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=raw, prefix="supplied")
            cache.set("k", "value")
            try:
                assert cache.get("k") == "value"
            finally:
                cache.delete("k")
        finally:
            raw.close()

    def test_sync_refuses_an_async_client(self, redis_connection_string: str):
        """The failure this prevents is silent, which is why it is a hard error.

        A sync adapter calling an async client's ``set`` gets a coroutine back,
        never awaits it, and returns normally — a write that reports success and
        did nothing. Both halves share the ``redis`` top-level name, so telling
        them apart takes more than one string comparison.
        """
        raw = redis.asyncio.Redis.from_url(redis_connection_string)
        try:
            cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=raw)
            with pytest.raises(TypeError, match="coroutine"):
                cache.set("k", "value")
        finally:
            pass


class TestWhatIsNotAccepted:
    def test_a_mongo_client_says_why(self):
        """Refused for a reason that will not go away, so the message says it."""
        import pymongo

        raw = pymongo.MongoClient("mongodb://localhost:27017")
        try:
            # A type checker rejects this too — the point is that the runtime
            # message explains itself rather than failing somewhere downstream.
            cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=raw)  # type: ignore[arg-type]
            with pytest.raises(TypeError, match=r"\?collection="):
                _ = cache.cache
        finally:
            raw.close()

    def test_a_non_client_is_rejected_at_construction(self):
        """A bad ``cache_url`` must fail where it was written, not on first use.

        Hoisting *anything* non-(str, Path) into ``cache_client`` would let
        ``cache_url=12345`` construct fine and fail on the first cache
        operation — potentially a deployment away from the misconfiguration.
        """
        with pytest.raises(pydantic.ValidationError):
            Cachetic[str](object_type=STR_ADAPTER, cache_url=12345)  # type: ignore[arg-type]

    def test_a_dump_without_its_client_is_refused(self, live_disk_cache: diskcache.Cache):
        """``model_dump()`` cannot carry a live connection, so it must not pretend to.

        The client is excluded from the dump — it is a connection, not
        configuration — leaving only the placeholder in ``cache_url``. That
        placeholder has no URL scheme, so a rebuilt model used to fall through
        every branch of the router to the **disk default** and silently build a
        cache in a directory named ``<cachetic supplied client: ...>``. Reads and
        writes then succeeded against a phantom backend while the caller's Redis
        sat untouched.
        """
        cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=live_disk_cache)
        dumped = cache.model_dump()

        assert "cache_client" not in dumped
        with pytest.raises(pydantic.ValidationError, match="supplied"):
            Cachetic[str](**dumped)

    def test_the_label_is_not_a_url(self, live_disk_cache: diskcache.Cache):
        """Nothing may parse it back into a scheme and route on it."""
        import urllib.parse

        cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=live_disk_cache)

        assert urllib.parse.urlparse(str(cache.cache_url)).scheme == ""
        assert "diskcache" in str(cache.cache_url)


class TestTheAdapterIsStillWhatComesBack:
    """Restoring the constructor does not restore ``.cache``."""

    def test_cache_returns_an_adapter_not_the_driver(self, live_disk_cache: diskcache.Cache):
        from cachetic.extensions.disk import DiskCacheAdapter

        cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=live_disk_cache)

        assert isinstance(cache.cache, DiskCacheAdapter)
        assert cache.cache is not live_disk_cache
