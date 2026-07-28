"""What a `Cachetic` is allowed to hold — [Principle 6](../docs/PRINCIPLES.md).

The rule is about a shape that is easy to lose one commit at a time: a memo
dictionary in front of the backend, a connection built in `__init__` "so the
first request is fast", a pool default raised because one benchmark liked it.
Each is reasonable alone and none of them is visible from a passing test suite —
they show up as a memory graph or a connection count in production, at a scale
nobody reproduces locally.

So they are measured here instead. These tests fail when Cachetic starts holding
something, not when it returns something wrong.

The threads are the part worth reading twice. `pymongo` keeps three monitor
threads per client and `psycopg_pool` keeps a scheduler and its workers — real
background work, started because Cachetic opened a client. The rule does not
forbid it, because forbidding it would mean dropping those drivers. What it
forbids is paying for it more than once, which is what sharing per URL buys, and
that is what `test_many_instances_build_one_client` pins.
"""

import pathlib
import threading
import uuid

import pydantic
import pytest

import cachetic
from cachetic import AsyncCachetic, Cachetic
from cachetic.aio import close_all as aio_close_all
from cachetic.extensions import _registry

STR_ADAPTER = pydantic.TypeAdapter(str)


class Person(pydantic.BaseModel):
    name: str


def unique_key(label: str) -> str:
    return f"discipline-{label}-{uuid.uuid4().hex}"


@pytest.fixture(autouse=True)
def empty_registry():
    """Each test measures the registry, so each starts from nothing."""
    cachetic.close_all()
    yield
    cachetic.close_all()


class TestNothingOpensEarly:
    """ "Before the first operation" is the whole of it — construction is free."""

    def test_constructing_a_client_opens_no_backend_client(self, backend_url: str | pathlib.Path):
        Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="discipline")

        assert _registry._registry_size() == 0

    def test_reaching_for_the_adapter_still_opens_nothing(self, backend_url: str | pathlib.Path):
        """Building the adapter is not connecting: it only holds an `EntryHandle`.

        This is what lets an application construct its caches at import time,
        including ones it may never use, without a connection per backend.
        """
        cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="discipline")
        _ = cache.cache

        assert _registry._registry_size() == 0

    def test_the_first_operation_opens_exactly_one(self, backend_url: str | pathlib.Path):
        cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="discipline")
        cache.get(unique_key("first"))

        assert _registry._registry_size() == 1


class TestTheCostIsPaidOncePerBackend:
    def test_many_instances_build_one_client(self, backend_url: str | pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """One cache per cached type is the normal shape, and must stay cheap.

        An application with twenty cached models builds twenty `Cachetic`
        objects. Twenty connection pools — and, for MongoDB, sixty monitor
        threads — is the failure this prevents.

        Counting the *factory calls* rather than the registry entries, because
        the two are not the same assertion: an `acquire` that rebuilt the client
        every time and stored it under the same key would leave the registry at
        one entry and open twenty connections. Only one of those is the rule.
        """
        builds = 0
        original = _registry.acquire

        def counting_acquire(namespace: str, url: str, *, factory, close):
            def counted():
                nonlocal builds
                builds += 1
                return factory()

            return original(namespace, url, factory=counted, close=close)

        monkeypatch.setattr(_registry, "acquire", counting_acquire)

        for index in range(20):
            cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix=f"discipline-{index}")
            cache.get(unique_key("shared"))

        assert builds == 1, f"built {builds} backend clients for one URL"
        assert _registry._registry_size() == 1

    def test_different_value_types_share_one_client(self, backend_url: str | pathlib.Path):
        """Sharing keys on the URL, not on `T` — the types are a client concern."""
        Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url).get(unique_key("s"))
        Cachetic[Person](object_type=pydantic.TypeAdapter(Person), cache_url=backend_url).get(unique_key("p"))

        assert _registry._registry_size() == 1

    async def test_the_async_client_shares_the_disk_handle_with_the_sync_one(self, tmp_path: pathlib.Path):
        """`diskcache` has no loop affinity, so both halves use one handle."""
        url = tmp_path.joinpath(".cachetic")
        Cachetic[str](object_type=STR_ADAPTER, cache_url=url).get(unique_key("sync"))
        await AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=url).get(unique_key("async"))
        try:
            assert _registry._registry_size() == 1
        finally:
            await aio_close_all()


class TestNoValueIsKeptInTheProcess:
    """The backend is the cache. Cachetic is not a second one in front of it."""

    def test_a_read_never_answers_from_memory(self, backend_url: str | pathlib.Path):
        """An in-process memo would make this stale read succeed.

        Two clients, one key: after the second deletes it, the first must miss.
        A memo dictionary keyed on the cache key — the obvious "optimisation" —
        would return the value it read a moment ago, and the cache would start
        serving data the backend no longer has.
        """
        key = unique_key("no-memo")
        reader = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="discipline")
        writer = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="discipline")

        writer.set(key, "value")
        assert reader.get(key) == "value"

        writer.delete(key)
        assert reader.get(key) is None

    def test_repeated_reads_of_a_miss_stay_misses(self, backend_url: str | pathlib.Path):
        """Negative caching is a memo too, and is not something to acquire quietly."""
        key = unique_key("no-negative-memo")
        reader = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="discipline")
        writer = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="discipline")

        assert reader.get(key) is None
        writer.set(key, "arrived-later")
        try:
            assert reader.get(key) == "arrived-later"
        finally:
            writer.delete(key)


class TestCacheticStartsNoThreadOfItsOwn:
    """Whatever runs in the background belongs to a driver, not to Cachetic.

    Only disk and Redis can be asserted on: `pymongo` and `psycopg_pool` keep
    threads of their own, which Principle 6 permits and
    `test_many_instances_build_one_client` bounds. What no backend excuses is
    Cachetic adding one — a refresh loop, a heartbeat, an eviction sweeper.
    """

    def test_disk_and_redis_add_nothing(self, tmp_path: pathlib.Path, redis_connection_string: str):
        before = {thread.name for thread in threading.enumerate()}

        for url in (tmp_path.joinpath(".cachetic"), redis_connection_string):
            cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=url, prefix="discipline")
            key = unique_key("threads")
            cache.set(key, "value")
            cache.get(key)
            cache.delete(key)

        assert {thread.name for thread in threading.enumerate()} - before == set()
