"""Process-wide registry for shared sync backend clients.

Deliberately the mirror image of :mod:`cachetic.extensions.aio._registry`: same
``Entry`` / ``EntryHandle`` / ``acquire`` / ``close_all`` shape, so what a
contributor learns about one half applies to the other. The differences are the
ones the sync world forces — entries are keyed by ``(namespace, url)`` with no
event loop involved, and the locks and close callbacks are ordinary blocking
ones.

Clients are shared across every ``Cachetic`` instance pointing at the same URL.
A single application typically builds several instances — one per cached type —
and each opening its own connections would be a waste with no upside.
"""

import dataclasses
import logging
import threading
import typing

__all__ = ["BUSY_REGISTRY_SIZE", "Entry", "EntryHandle", "acquire", "close_all"]

logger = logging.getLogger("cachetic")

CloseFn = typing.Callable[[typing.Any], None]

_RegistryKey = tuple[str, str]

BUSY_REGISTRY_SIZE = 8
"""Entry count above which a registry is more likely a bug than a deployment.

Entries are keyed by URL and only removed by ``close_all`` — or, in the async
registry, by a loop closing — so a URL built per request or per tenant grows
without bound, each entry holding connections. Real deployments talk to a
handful of backends. Shared by both registries so the two cannot disagree.
"""


@dataclasses.dataclass
class Entry:
    """A client shared by every adapter using the same backend and URL."""

    client: typing.Any
    close: CloseFn
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    ensured: set[typing.Any] = dataclasses.field(default_factory=set)
    """Setup steps already performed for this client (index names, tables, ...)."""


_registry: dict[_RegistryKey, Entry] = {}
_registry_lock = threading.Lock()
_warned_busy = False


class EntryHandle:
    """Resolves an adapter's registry entry on every operation.

    Adapters must not pin an :class:`Entry` at construction time. Doing so
    outlives the registry: after :func:`close_all` the adapter would keep using
    a client that has been closed, and every later call on it would fail for the
    rest of the process. Re-resolving costs one lock and one dict lookup, and
    transparently rebuilds the client when it has gone away.
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
        """Returns the live entry for this backend and URL, creating it if needed."""
        return acquire(
            self._namespace, self._url, factory=self._factory, close=self._close
        )


def _warn_if_busy_locked() -> None:
    """Warns once if the registry has grown past what a deployment explains."""
    global _warned_busy
    if _warned_busy or len(_registry) <= BUSY_REGISTRY_SIZE:
        return
    _warned_busy = True
    logger.warning(
        "Cachetic is holding %d shared backend clients. Clients are keyed by "
        "connection URL and are never evicted, so a URL that varies per request "
        "or per tenant will keep opening connections. Reuse a fixed set of URLs.",
        len(_registry),
    )


def acquire(
    namespace: str,
    url: str,
    *,
    factory: typing.Callable[[], typing.Any],
    close: CloseFn,
) -> Entry:
    """Returns the shared entry for ``(namespace, url)``.

    ``factory`` is called at most once per key. The lock makes the
    check-then-act atomic: without it two threads racing a cold cache each build
    a client, and for PostgreSQL each then races a ``CREATE TABLE IF NOT
    EXISTS`` — which is not atomic across concurrent transactions.
    """
    key: _RegistryKey = (namespace, url)

    with _registry_lock:
        entry: Entry | None = _registry.get(key)
        if entry is None:
            entry = Entry(client=factory(), close=close)
            _registry[key] = entry
            logger.debug("Created new %s client for: %s", namespace, url)
            _warn_if_busy_locked()
        else:
            logger.debug("Reusing existing %s client for: %s", namespace, url)
        return entry


def close_all() -> None:
    """Closes every shared sync client and empties the registry.

    Call it before a process forks, at shutdown, or between test cases. It is
    safe to call more than once and safe to call when nothing is open. Adapters
    hold an :class:`EntryHandle` rather than an ``Entry``, so a client used again
    afterwards reconnects instead of failing.

    Operations running on another thread are not waited for; closing a client
    out from under one surfaces a driver error on that thread.
    """
    with _registry_lock:
        keys: list[_RegistryKey] = list(_registry)
        entries: list[Entry] = [_registry.pop(key) for key in keys]

    for key, entry in zip(keys, entries, strict=True):
        try:
            entry.close(entry.client)
        except Exception:  # shutdown must never raise
            logger.warning("Failed to close %s client for: %s", key[0], key[1])
        else:
            logger.debug("Closed %s client for: %s", key[0], key[1])


def _registry_size() -> int:
    """Returns the number of live entries. Intended for tests."""
    with _registry_lock:
        return len(_registry)
