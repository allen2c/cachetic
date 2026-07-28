"""Per-event-loop registry for shared async backend clients.

Async drivers bind to the event loop that created their connection pool: a
client built under one loop raises ``got Future attached to a different loop``
if reused from another. Entries are therefore keyed by ``(loop, namespace, url)``
rather than by URL alone.

Two different locks are used on purpose:

* A :class:`threading.Lock` guards the registry itself. Every client factory here
  is *synchronous* (``Redis.from_url``, ``AsyncMongoClient(...)``,
  ``AsyncConnectionPool(..., open=False)``), so the lock is never held across an
  ``await`` and remains safe when several threads each run their own loop.
* An :class:`asyncio.Lock` stored *inside* each entry guards the awaitable setup
  step (index creation, ``CREATE TABLE``, opening a pool). Because it is created
  under the loop that owns the entry it is never shared across loops.

A :class:`weakref.WeakKeyDictionary` keyed by loop would not work here: every
async client stores a reference back to its loop, so the value keeps the key
alive and entries are never collected. Dead loops are instead swept explicitly,
on every :func:`acquire` — which, because adapters resolve through
:class:`EntryHandle`, means on every cache operation.

Sweeping drops the reference; it cannot close the client. Every close path these
drivers offer (``Redis.aclose``, ``AsyncMongoClient.close``,
``AsyncConnectionPool.close``) is a coroutine, and there is no live loop left to
run it on. Releasing the socket is therefore up to the garbage collector, and
the sweep cannot happen at all while the process makes no cache calls. Call
:func:`cachetic.aio.close_all` before a loop exits and none of that applies.
"""

import asyncio
import dataclasses
import logging
import threading
import typing

from cachetic.extensions import _registry as _sync_registry
from cachetic.extensions._registry import BUSY_REGISTRY_SIZE
from cachetic.utils.hide_url_password import hide_url_password

__all__ = ["BUSY_REGISTRY_SIZE", "Entry", "EntryHandle", "acquire", "close_all"]

logger = logging.getLogger("cachetic")

CloseFn = typing.Callable[[typing.Any], typing.Awaitable[None]]

_RegistryKey = tuple[asyncio.AbstractEventLoop, str, str]

# Forward-referenced: Entry is defined below, after the public functions.
_registry: dict[_RegistryKey, "Entry"] = {}
_registry_lock = threading.Lock()
_warned_unclosed = False
_warned_busy = False


def acquire(
    namespace: str,
    url: str,
    *,
    factory: typing.Callable[[], typing.Any],
    close: CloseFn,
) -> "Entry":
    """Returns the shared entry for ``(running loop, namespace, url)``.

    ``factory`` must be synchronous and is called at most once per key. Must be
    called from within a running event loop.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    key: _RegistryKey = (loop, namespace, url)

    with _registry_lock:
        _sweep_closed_loops_locked()
        entry: Entry | None = _registry.get(key)
        if entry is None:
            entry = Entry(client=factory(), close=close)
            _registry[key] = entry
            # Masked at every log site: the key has to stay the raw URL, but for
            # Redis, MongoDB and PostgreSQL that raw URL carries the password.
            logger.debug("Created new %s client for: %s", namespace, hide_url_password(url))
            _warn_if_busy_locked()
        else:
            logger.debug("Reusing existing %s client for: %s", namespace, hide_url_password(url))
        return entry


async def close_all() -> None:
    """Closes every shared client belonging to the running event loop.

    Clients created under other loops are left untouched — they can only be
    awaited from the loop that owns them.

    Disk caches are the exception and are released unconditionally: ``diskcache``
    has no async API, so async disk adapters share the *synchronous* registry's
    handles and there is no per-loop entry here to find. Without this an
    application that only ever calls the async teardown would never close them.
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    with _registry_lock:
        _sweep_closed_loops_locked()
        keys: list[_RegistryKey] = [key for key in _registry if key[0] is loop]
        entries: list[Entry] = [_registry.pop(key) for key in keys]

    for key, entry in zip(keys, entries, strict=True):
        try:
            await entry.close(entry.client)
        except Exception:  # shutdown must never raise
            logger.warning("Failed to close %s client for: %s", key[1], hide_url_password(key[2]))
        else:
            logger.debug("Closed %s client for: %s", key[1], hide_url_password(key[2]))

    _sync_registry._close_namespace(_sync_registry.DISK_NAMESPACE)


@dataclasses.dataclass
class Entry:
    """A client shared by every adapter using the same loop, backend and URL."""

    client: typing.Any
    close: CloseFn
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    ensured: set[typing.Any] = dataclasses.field(default_factory=set)
    """Setup steps already performed for this client (index names, tables, ...)."""


class EntryHandle:
    """Resolves an adapter's registry entry on every operation.

    Adapters must not pin an :class:`Entry` at construction time. Doing so
    outlives the registry: after :func:`close_all` the adapter would keep using
    a client that has been closed, and every later call on it would fail for as
    long as the loop stays alive. Re-resolving costs one lock and one dict
    lookup, and transparently rebuilds the client when it has gone away.
    """

    def __init__(
        self,
        namespace: str,
        url: str,
        *,
        factory: typing.Callable[[], typing.Any],
        close: CloseFn,
    ) -> None:
        self._namespace = namespace
        self._url = url
        self._factory = factory
        self._close = close

    def __call__(self) -> Entry:
        """Returns the live entry for the running loop, creating it if needed."""
        return acquire(self._namespace, self._url, factory=self._factory, close=self._close)


def _sweep_closed_loops_locked() -> None:
    """Drops entries whose event loop has been closed.

    Their clients cannot be awaited from another loop, so the reference is
    released and the socket is left to the garbage collector. Calling
    :func:`cachetic.aio.close_all` before a loop exits avoids this.

    The first occurrence is reported at WARNING: it means connections were held
    open past the point the caller could still close them, which is worth
    surfacing rather than burying in debug output.
    """
    global _warned_unclosed
    dead: list[_RegistryKey] = [key for key in _registry if key[0].is_closed()]
    for key in dead:
        _registry.pop(key, None)
        if not _warned_unclosed:
            _warned_unclosed = True
            logger.warning(
                "Dropped a %s client whose event loop had already closed (%s) "
                "without closing it — its connections are left to the garbage "
                "collector. Await cachetic.aio.close_all() before the loop "
                "exits to release them deterministically.",
                key[1],
                hide_url_password(key[2]),
            )
        else:
            logger.debug(
                "Dropped %s client for a closed event loop without closing it: %s",
                key[1],
                hide_url_password(key[2]),
            )


def _warn_if_busy_locked() -> None:
    """Warns once if the registry has grown past what a deployment explains."""
    global _warned_busy
    if _warned_busy or len(_registry) <= BUSY_REGISTRY_SIZE:
        return
    _warned_busy = True
    logger.warning(
        "Cachetic is holding %d shared backend clients. Clients are keyed by "
        "connection URL and are only evicted when their event loop closes, so a "
        "URL that varies per request or per tenant will keep opening "
        "connections. Reuse a fixed set of URLs.",
        len(_registry),
    )


def _registry_size() -> int:
    """Returns the number of live entries. Intended for tests."""
    with _registry_lock:
        return len(_registry)
