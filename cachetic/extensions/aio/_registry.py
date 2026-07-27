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
alive and entries are never collected. Dead loops are instead swept explicitly.
"""

import asyncio
import dataclasses
import logging
import threading
import typing

logger = logging.getLogger("cachetic")

CloseFn = typing.Callable[[typing.Any], typing.Awaitable[None]]

_RegistryKey = tuple[asyncio.AbstractEventLoop, str, str]


@dataclasses.dataclass
class Entry:
    """A client shared by every adapter using the same loop, backend and URL."""

    client: typing.Any
    close: CloseFn
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    ensured: set[typing.Any] = dataclasses.field(default_factory=set)
    """Setup steps already performed for this client (index names, tables, ...)."""


_registry: dict[_RegistryKey, Entry] = {}
_registry_lock = threading.Lock()


def _sweep_closed_loops_locked() -> None:
    """Drops entries whose event loop has been closed.

    Their clients cannot be awaited from another loop, so the reference is
    released and the socket is left to the garbage collector. Calling
    :func:`cachetic.aio.close_all` before a loop exits avoids this.
    """
    dead: list[_RegistryKey] = [key for key in _registry if key[0].is_closed()]
    for key in dead:
        _registry.pop(key, None)
        logger.debug(
            "Dropped %s client for a closed event loop without closing it: %s",
            key[1],
            key[2],
        )


def acquire(
    namespace: str,
    url: str,
    *,
    factory: typing.Callable[[], typing.Any],
    close: CloseFn,
) -> Entry:
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
            logger.debug("Created new %s client for: %s", namespace, url)
        else:
            logger.debug("Reusing existing %s client for: %s", namespace, url)
        return entry


async def close_all() -> None:
    """Closes every shared client belonging to the running event loop.

    Clients created under other loops are left untouched — they can only be
    awaited from the loop that owns them.
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
            logger.warning("Failed to close %s client for: %s", key[1], key[2])
        else:
            logger.debug("Closed %s client for: %s", key[1], key[2])


def _registry_size() -> int:
    """Returns the number of live entries. Intended for tests."""
    with _registry_lock:
        return len(_registry)
