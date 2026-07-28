"""Lifecycle of the shared sync backend clients.

The sync registry is the mirror image of the async one, and these tests are
deliberately the mirror of `test_async_registry.py`: same client sharing, same
teardown, same recovery after teardown.
"""

import logging
import pathlib
import threading
import typing
import uuid

import pydantic
import pytest

from cachetic import Cachetic, close_all
from cachetic.extensions import _registry
from cachetic.types.cache_protocol import CacheProtocol

STR_ADAPTER = pydantic.TypeAdapter(str)


def unique_key(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex}"


def entry_of(cache: CacheProtocol) -> _registry.Entry:
    """Returns the registry entry an adapter currently resolves to.

    Adapters hold an ``EntryHandle`` and re-resolve on every operation rather
    than pinning an ``Entry`` (see docs/architecture.md), and ``CacheProtocol``
    deliberately does not expose it. Reaching past the protocol is the point of
    these tests, so the cast is confined here."""
    return typing.cast(typing.Any, cache)._entry()


@pytest.fixture(autouse=True)
def _isolated_registry():
    close_all()
    yield
    close_all()


def test_same_url_shares_one_client(backend_url: str | pathlib.Path):
    """Two instances on one URL must reuse a single client."""
    first = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="share-a")
    second = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="share-b")
    key = unique_key("share")

    first.set(key, "value")
    assert second.get(f"{key}") is None  # different prefix, same client
    assert entry_of(first.cache) is entry_of(second.cache)
    assert _registry._registry_size() == 1

    first.delete(key)


def test_separate_urls_get_separate_entries(redis_connection_string: str, mongo_connection_string: str):
    """Different backends must not share a client."""
    redis_cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=redis_connection_string, prefix="multi")
    mongo_cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=mongo_connection_string, prefix="multi")
    key = unique_key("multi")

    try:
        redis_cache.set(key, "redis-value")
        mongo_cache.set(key, "mongo-value")

        assert redis_cache.get(key) == "redis-value"
        assert mongo_cache.get(key) == "mongo-value"
        assert _registry._registry_size() == 2
    finally:
        redis_cache.delete(key)
        mongo_cache.delete(key)


def test_close_all_releases_entries(backend_url: str | pathlib.Path):
    """close_all must empty the registry."""
    cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="closeall")
    key = unique_key("close")

    cache.set(key, "value")
    assert _registry._registry_size() >= 1

    cache.delete(key)
    close_all()
    assert _registry._registry_size() == 0


def test_close_all_is_idempotent(backend_url: str | pathlib.Path):
    """Calling it twice, or with nothing open, must not raise."""
    close_all()
    close_all()

    cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="idempotent")
    cache.set(unique_key("idem"), "value")
    close_all()
    close_all()


def test_instance_recovers_after_close_all(backend_url: str | pathlib.Path):
    """A client reused after close_all must reconnect, not fail forever.

    Adapters resolve their registry entry per operation rather than pinning one
    that close_all has already closed — the sync counterpart of
    `test_instance_recovers_after_close_all_on_a_live_loop`.
    """
    cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="reopen")
    key = unique_key("reopen")

    try:
        cache.set(key, "before")
        assert cache.get(key) == "before"

        close_all()

        assert cache.get(key) == "before"
        cache.set(key, "after")
        assert cache.get(key) == "after"
    finally:
        cache.delete(key)


def test_concurrent_construction_shares_one_client(backend_url: str | pathlib.Path):
    """Threads racing a cold registry must end up with a single client.

    Without the registry lock this check-then-act builds one client per thread,
    and for PostgreSQL races two CREATE TABLE statements — which IF NOT EXISTS
    does not make atomic across concurrent transactions.
    """
    barrier = threading.Barrier(8)
    failures: list[str] = []

    def worker(index: int) -> None:
        try:
            cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix="race")
            barrier.wait(timeout=30)
            cache.set(unique_key(f"race-{index}"), "value")
        except BaseException as error:  # reported after the join below
            failures.append(f"{type(error).__name__}: {error}")

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert [t for t in threads if t.is_alive()] == [], "worker thread hung"
    assert failures == []
    assert _registry._registry_size() == 1


def test_registry_never_logs_a_password(caplog: pytest.LogCaptureFixture):
    """Adapters redact their own log lines; the registry has to redact its key.

    That key is the raw URL — it has to be, to identify the client — and for
    Redis, MongoDB and PostgreSQL the raw URL carries the password. DEBUG
    logging, or a close that fails and reports at WARNING, would otherwise write
    the credentials out in plaintext.
    """
    secret = "TOPSECRET"
    url = f"redis://user:{secret}@localhost:6379/0"

    def failing_close(_client: object) -> None:
        raise RuntimeError("close failed")  # forces the WARNING path

    with caplog.at_level(logging.DEBUG, logger="cachetic"):
        _registry.acquire("fake", url, factory=object, close=failing_close)
        _registry.acquire("fake", url, factory=object, close=failing_close)  # reuse path
        close_all()

    assert caplog.text, "no log records captured — the assertions below prove nothing"
    assert secret not in caplog.text
    assert "redis://user:***@localhost:6379/0" in caplog.text


def test_sync_and_async_registries_expose_the_same_api():
    """The two halves must stay symmetric — a contributor learns one, not two."""
    from cachetic.extensions.aio import _registry as aio_registry

    shared = {"Entry", "EntryHandle", "acquire", "close_all", "BUSY_REGISTRY_SIZE"}
    assert shared <= set(dir(_registry))
    assert shared <= set(dir(aio_registry))
