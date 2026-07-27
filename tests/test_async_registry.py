"""Event-loop affinity of the shared async client registry.

Async drivers bind to the loop that created them, so a client cached across
loops is a latent deadlock. These tests pin the two failure modes the registry
design exists to prevent.
"""

import asyncio
import threading
import uuid

import pydantic

from cachetic import AsyncCachetic
from cachetic.aio import close_all
from cachetic.extensions.aio import _registry

STR_ADAPTER = pydantic.TypeAdapter(str)


def unique_key(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex}"


def test_instance_survives_sequential_event_loops(redis_connection_string: str):
    """One client reused across two asyncio.run() calls must keep working.

    Each asyncio.run creates and closes its own loop. A registry keyed by URL
    alone would hand the second loop a client bound to the first, closed one.
    """
    cache = AsyncCachetic[str](
        object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="loops"
    )
    key = unique_key("sequential")

    async def roundtrip(value: str) -> str | None:
        await cache.set(key, value)
        try:
            return await cache.get(key)
        finally:
            await close_all()

    assert asyncio.run(roundtrip("first")) == "first"
    assert asyncio.run(roundtrip("second")) == "second"

    asyncio.run(_cleanup(cache, key))


async def _cleanup(cache: AsyncCachetic[str], key: str) -> None:
    await cache.delete(key)
    await close_all()


def test_concurrent_loops_in_threads_do_not_deadlock(redis_connection_string: str):
    """Threads that each run their own loop must not block one another.

    A single asyncio.Lock shared between instances would deadlock here: it is
    not thread-safe, and awaiting one bound to another loop hangs forever.
    """
    cache = AsyncCachetic[str](
        object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="threads"
    )
    keys = [unique_key(f"thread-{index}") for index in range(3)]
    failures: list[tuple[int, str]] = []

    def worker(index: int) -> None:
        async def go() -> None:
            await cache.set(keys[index], f"value-{index}")
            assert await cache.get(keys[index]) == f"value-{index}"
            await cache.delete(keys[index])
            await close_all()

        try:
            asyncio.run(go())
        except BaseException as error:  # reported after the join below
            failures.append((index, f"{type(error).__name__}: {error}"))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert [t for t in threads if t.is_alive()] == [], "worker thread deadlocked"
    assert failures == []


def test_close_all_releases_entries(redis_connection_string: str):
    """close_all must empty the registry for the loop that calls it."""
    cache = AsyncCachetic[str](
        object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="closeall"
    )
    key = unique_key("close")

    async def go() -> tuple[int, int]:
        await cache.set(key, "value")
        before = _registry._registry_size()
        await cache.delete(key)
        await close_all()
        return before, _registry._registry_size()

    before, after = asyncio.run(go())

    assert before >= 1
    assert after == 0


def test_closed_loop_entries_are_swept(redis_connection_string: str):
    """Entries from a closed loop must not accumulate.

    A WeakKeyDictionary cannot do this: async clients hold a reference back to
    their loop, so the value keeps the weak key alive forever.
    """
    cache = AsyncCachetic[str](
        object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="sweep"
    )
    key = unique_key("sweep")

    async def leak() -> int:
        # Deliberately no close_all(): the loop closes with the entry in place.
        await cache.set(key, "value")
        return _registry._registry_size()

    assert asyncio.run(leak()) >= 1

    async def observe() -> int:
        # A fresh loop: acquiring sweeps the previous loop's dead entry.
        await cache.set(key, "value")
        size = _registry._registry_size()
        await cache.delete(key)
        await close_all()
        return size

    assert asyncio.run(observe()) == 1


async def test_separate_urls_get_separate_entries(
    redis_connection_string: str, mongo_connection_string: str
):
    """Different backends on one loop must not share a client."""
    await close_all()

    redis_cache = AsyncCachetic[str](
        object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="multi"
    )
    mongo_cache = AsyncCachetic[str](
        object_type=STR_ADAPTER, cache_url=mongo_connection_string, prefix="multi"
    )
    key = unique_key("multi")

    try:
        await redis_cache.set(key, "redis-value")
        await mongo_cache.set(key, "mongo-value")

        assert await redis_cache.get(key) == "redis-value"
        assert await mongo_cache.get(key) == "mongo-value"
        assert _registry._registry_size() == 2
    finally:
        await redis_cache.delete(key)
        await mongo_cache.delete(key)
        await close_all()


async def test_same_url_shares_one_client(redis_connection_string: str):
    """Two instances on one loop and URL must reuse a single client."""
    await close_all()

    first = AsyncCachetic[str](
        object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="share-a"
    )
    second = AsyncCachetic[str](
        object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="share-b"
    )

    try:
        first_cache = await first.cache()
        second_cache = await second.cache()

        assert first_cache._entry is second_cache._entry
        assert _registry._registry_size() == 1
    finally:
        await close_all()
