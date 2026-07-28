"""Behavioural parity between AsyncCachetic and Cachetic.

Every test runs once per backend via the ``backend_url`` fixture. Where the
sync and async clients are expected to behave identically, both are exercised
against the same assertions so the two cannot drift.
"""

import asyncio
import pathlib
import uuid

import pydantic
import pytest

from cachetic import AsyncCachetic, CacheNotFoundError, Cachetic
from cachetic.aio import close_all


class Person(pydantic.BaseModel):
    name: str
    age: int


PERSON_ADAPTER = pydantic.TypeAdapter(Person)
ALICE = Person(name="Alice", age=30)
BOB = Person(name="Bob", age=25)


def unique_key(label: str) -> str:
    """Returns a key unique to this test run.

    Backends are shared between tests and between runs, so fixed keys would let
    one test observe another's leftovers.
    """
    return f"{label}-{uuid.uuid4().hex}"


@pytest.fixture
async def async_cache(backend_url: str | pathlib.Path):
    """An AsyncCachetic bound to the parametrized backend, closed on teardown."""
    cache = AsyncCachetic[Person](
        object_type=PERSON_ADAPTER,
        cache_url=backend_url,
        prefix="asynctest",
    )
    try:
        yield cache
    finally:
        await close_all()


def sync_cache_for(backend_url: str | pathlib.Path) -> Cachetic[Person]:
    """A Cachetic pointed at the same place, with the same key prefix."""
    return Cachetic[Person](
        object_type=PERSON_ADAPTER,
        cache_url=backend_url,
        prefix="asynctest",
    )


class TestLifecycle:
    """set / get / exists / delete across every backend."""

    async def test_get_missing_returns_none(self, async_cache: AsyncCachetic[Person]):
        assert await async_cache.get(unique_key("missing")) is None

    async def test_exists_missing_returns_false(self, async_cache: AsyncCachetic[Person]):
        assert await async_cache.exists(unique_key("missing")) is False

    async def test_set_then_get(self, async_cache: AsyncCachetic[Person]):
        key = unique_key("person")
        await async_cache.set(key, ALICE)

        assert await async_cache.get(key) == ALICE
        assert await async_cache.exists(key) is True

    async def test_set_overwrites(self, async_cache: AsyncCachetic[Person]):
        key = unique_key("person")
        await async_cache.set(key, ALICE)
        await async_cache.set(key, BOB)

        assert await async_cache.get(key) == BOB

    async def test_delete(self, async_cache: AsyncCachetic[Person]):
        key = unique_key("person")
        await async_cache.set(key, ALICE)
        await async_cache.delete(key)

        assert await async_cache.get(key) is None
        assert await async_cache.exists(key) is False

    async def test_delete_missing_is_noop(self, async_cache: AsyncCachetic[Person]):
        await async_cache.delete(unique_key("never-written"))

    async def test_get_or_raise_hit(self, async_cache: AsyncCachetic[Person]):
        key = unique_key("person")
        await async_cache.set(key, ALICE)

        assert await async_cache.get_or_raise(key) == ALICE

    async def test_get_or_raise_miss(self, async_cache: AsyncCachetic[Person]):
        with pytest.raises(CacheNotFoundError):
            await async_cache.get_or_raise(unique_key("missing"))


class TestTTL:
    """TTL semantics, matching the sync client."""

    async def test_zero_ttl_skips_the_write(self, async_cache: AsyncCachetic[Person]):
        key = unique_key("zero-ttl")
        await async_cache.set(key, ALICE, ex=0)

        assert await async_cache.get(key) is None

    async def test_negative_ttl_never_expires(self, async_cache: AsyncCachetic[Person]):
        key = unique_key("no-expiry")
        await async_cache.set(key, ALICE, ex=-1)
        await asyncio.sleep(1.2)

        assert await async_cache.get(key) == ALICE

    async def test_positive_ttl_expires(self, async_cache: AsyncCachetic[Person]):
        """Entries do expire, allowing for the documented lateness.

        MongoDB and PostgreSQL derive a whole-second deadline from a truncated
        clock and compare it with a strict ``<``, so an entry can outlive its
        TTL by up to a second. The wait below is deliberately generous.
        """
        key = unique_key("expiring")
        await async_cache.set(key, ALICE, ex=1)
        assert await async_cache.get(key) == ALICE

        await asyncio.sleep(2.5)
        assert await async_cache.get(key) is None
        assert await async_cache.exists(key) is False


class TestKeyPrefix:
    """The prefix is applied by the shared base, so it must match sync exactly."""

    async def test_prefix_is_applied(self, async_cache: AsyncCachetic[Person]):
        assert async_cache.get_cache_key("key") == "asynctest:key"

    async def test_prefix_isolates_instances(self, backend_url: str | pathlib.Path) -> None:
        key = unique_key("shared-name")
        first = AsyncCachetic[Person](object_type=PERSON_ADAPTER, cache_url=backend_url, prefix="tenant-a")
        second = AsyncCachetic[Person](object_type=PERSON_ADAPTER, cache_url=backend_url, prefix="tenant-b")
        try:
            await first.set(key, ALICE)

            assert await first.get(key) == ALICE
            assert await second.get(key) is None
        finally:
            await first.delete(key)
            await close_all()


class TestCrossClientInterop:
    """A value written by one client must be readable by the other.

    Both clients share CacheticBase's serialisation, so this pins the promise
    that adding the async client did not fork the storage format.
    """

    async def test_async_write_sync_read(self, async_cache: AsyncCachetic[Person], backend_url: str | pathlib.Path):
        key = unique_key("a2s")
        await async_cache.set(key, ALICE)

        assert sync_cache_for(backend_url).get(key) == ALICE

    async def test_sync_write_async_read(self, async_cache: AsyncCachetic[Person], backend_url: str | pathlib.Path):
        key = unique_key("s2a")
        sync_cache_for(backend_url).set(key, BOB)

        assert await async_cache.get(key) == BOB

    async def test_async_delete_visible_to_sync(
        self, async_cache: AsyncCachetic[Person], backend_url: str | pathlib.Path
    ):
        key = unique_key("adel")
        sync_cache = sync_cache_for(backend_url)
        sync_cache.set(key, ALICE)

        await async_cache.delete(key)

        assert sync_cache.get(key) is None

    async def test_identical_serialised_bytes(
        self, async_cache: AsyncCachetic[Person], backend_url: str | pathlib.Path
    ):
        """Both clients must produce byte-identical payloads for one value."""
        sync_cache = sync_cache_for(backend_url)

        assert async_cache._dump_any(ALICE) == sync_cache._dump_any(ALICE)


class TestCompression:
    """Compression is handled by the shared base, but must survive the round trip."""

    async def test_compressed_round_trip(self, backend_url: str | pathlib.Path):
        key = unique_key("compressed")
        cache = AsyncCachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=backend_url,
            prefix="asynctest",
            compression=True,
        )
        try:
            await cache.set(key, ALICE)
            assert await cache.get(key) == ALICE
        finally:
            await cache.delete(key)
            await close_all()

    async def test_compressed_write_read_by_uncompressed_client(self, backend_url: str | pathlib.Path):
        """Readers auto-detect compression regardless of their own setting."""
        key = unique_key("mixed")
        writer = AsyncCachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=backend_url,
            prefix="asynctest",
            compression=True,
        )
        reader = AsyncCachetic[Person](
            object_type=PERSON_ADAPTER,
            cache_url=backend_url,
            prefix="asynctest",
            compression=False,
        )
        try:
            await writer.set(key, ALICE)
            assert await reader.get(key) == ALICE
        finally:
            await writer.delete(key)
            await close_all()


class TestObjectTypes:
    """The generic parameter behaves the same as on the sync client."""

    async def test_bytes_type(self, backend_url: str | pathlib.Path):
        key = unique_key("raw-bytes")
        cache = AsyncCachetic[bytes](
            object_type=pydantic.TypeAdapter(bytes),
            cache_url=backend_url,
            prefix="asynctest",
        )
        try:
            await cache.set(key, b"\x00\x01\x02binary")
            assert await cache.get(key) == b"\x00\x01\x02binary"
        finally:
            await cache.delete(key)
            await close_all()

    async def test_list_of_models(self, backend_url: str | pathlib.Path):
        key = unique_key("people")
        cache = AsyncCachetic[list[Person]](
            object_type=pydantic.TypeAdapter(list[Person]),
            cache_url=backend_url,
            prefix="asynctest",
        )
        try:
            await cache.set(key, [ALICE, BOB])
            result = await cache.get(key)

            assert result == [ALICE, BOB]
            assert all(isinstance(person, Person) for person in result or [])
        finally:
            await cache.delete(key)
            await close_all()
