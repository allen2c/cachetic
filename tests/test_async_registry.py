"""Event-loop affinity of the shared async client registry.

Async drivers bind to the loop that created them, so a client cached across
loops is a latent deadlock. These tests pin the two failure modes the registry
design exists to prevent.
"""

import asyncio
import pathlib
import sys
import threading
import typing
import uuid

import pydantic

import cachetic
from cachetic import AsyncCachetic
from cachetic.aio import close_all
from cachetic.extensions import _registry as sync_registry
from cachetic.extensions.aio import _registry
from cachetic.types.async_cache_protocol import AsyncCacheProtocol

STR_ADAPTER = pydantic.TypeAdapter(str)


def unique_key(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex}"


def entry_of(cache: AsyncCacheProtocol) -> _registry.Entry:
    """Returns the registry entry an adapter currently resolves to.

    Adapters hold an ``EntryHandle`` and re-resolve on every operation rather
    than pinning an ``Entry`` (see docs/architecture.md), and
    ``AsyncCacheProtocol`` deliberately does not expose it. Reaching past the
    protocol is the point of these tests, so the cast is confined here."""
    return typing.cast(typing.Any, cache)._entry()


def test_instance_survives_sequential_event_loops(redis_connection_string: str):
    """One client reused across two asyncio.run() calls must keep working.

    Each asyncio.run creates and closes its own loop. A registry keyed by URL
    alone would hand the second loop a client bound to the first, closed one.
    """
    cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="loops")
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
    cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="threads")
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


class _ClosedLoop:
    """Stands in for an event loop that has already closed.

    The sweep only ever asks a key whether it is closed, and building hundreds
    of real loops to seed the race would cost far more than it proves.
    """

    def is_closed(self) -> bool:
        return True


def test_sweeping_dead_loops_does_not_race_a_concurrent_insert(tmp_path: pathlib.Path):
    """One thread sweeping dead loops must not collide with another inserting one.

    ``AsyncCachetic._caches`` is a single dict shared by every thread that uses
    the instance — the worker-pool pattern the module docstring advertises. An
    unguarded sweep walks it while another thread's first cache call inserts the
    loop it has just created, which raises ``RuntimeError: dictionary changed
    size during iteration``.

    ``test_concurrent_loops_in_threads_do_not_deadlock`` cannot catch this: all
    of its loops stay alive, so the sweep branch never runs at all.
    """
    cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=tmp_path.joinpath(".cachetic"))
    filler = typing.cast(typing.Any, object())
    failures: list[str] = []
    threads_per_round = 8

    def worker(gate: threading.Barrier) -> None:
        try:
            gate.wait(timeout=30)
            asyncio.run(cache.cache())
        except BaseException as error:  # reported after the join below
            failures.append(f"{type(error).__name__}: {error}")

    # A short switch interval preempts the sweep mid-walk instead of letting it
    # finish inside one scheduling slice, which is what makes the race land.
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for _ in range(12):
            # Reseed every round: the first successful sweep empties these.
            for _ in range(200):
                dead = typing.cast(asyncio.AbstractEventLoop, _ClosedLoop())
                cache._caches[dead] = filler

            gate = threading.Barrier(threads_per_round)
            threads = [threading.Thread(target=worker, args=(gate,)) for _ in range(threads_per_round)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            assert [t for t in threads if t.is_alive()] == [], "worker thread hung"
            if failures:
                break
    finally:
        sys.setswitchinterval(previous_interval)
        cache._caches.clear()

    assert failures == []


async def test_close_all_releases_disk_handles(tmp_path: pathlib.Path):
    """The async teardown must release disk caches too.

    ``diskcache`` has no async API, so async disk adapters share the
    *synchronous* registry's handles and ``close_all`` has no per-loop entry to
    find. An application that only ever awaits ``cachetic.aio.close_all()`` —
    the pattern every doc surface shows — would otherwise never close them.
    """
    cachetic.close_all()

    cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=tmp_path.joinpath(".cachetic"))
    await cache.set(unique_key("disk"), "value")
    assert _disk_entry_count() == 1

    await close_all()

    assert _disk_entry_count() == 0


def _disk_entry_count() -> int:
    """Counts the shared ``diskcache.Cache`` handles the sync registry holds."""
    return len([key for key in sync_registry._registry if key[0] == sync_registry.DISK_NAMESPACE])


def test_close_all_releases_entries(redis_connection_string: str):
    """close_all must empty the registry for the loop that calls it."""
    cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="closeall")
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
    cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="sweep")
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


async def test_separate_urls_get_separate_entries(redis_connection_string: str, mongo_connection_string: str):
    """Different backends on one loop must not share a client."""
    await close_all()

    redis_cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="multi")
    mongo_cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=mongo_connection_string, prefix="multi")
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

    first = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="share-a")
    second = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="share-b")

    try:
        first_cache = await first.cache()
        second_cache = await second.cache()

        assert entry_of(first_cache) is entry_of(second_cache)
        assert _registry._registry_size() == 1
    finally:
        await close_all()


async def test_instance_recovers_after_close_all_on_a_live_loop(
    backend_url: str,
) -> None:
    """A client reused after close_all() must reconnect, not fail forever.

    close_all() is documented as loop teardown, but nothing stops it being
    called while the loop keeps running — a shutdown hook that races a
    straggler request, or a test that resets between cases. Adapters therefore
    resolve their registry entry per operation instead of pinning one that
    close_all() has already closed.
    """
    await close_all()

    cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="reopen")
    key = unique_key("reopen")

    try:
        await cache.set(key, "before")
        assert await cache.get(key) == "before"

        await close_all()

        assert await cache.get(key) == "before"
        await cache.set(key, "after")
        assert await cache.get(key) == "after"
    finally:
        await cache.delete(key)
        await close_all()
