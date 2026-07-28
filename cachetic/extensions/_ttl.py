"""The one definition of what a backend does with the ``ex`` it is handed.

[Principle 2](../../docs/PRINCIPLES.md) requires every backend to answer the same
call the same way, and ``Cachetic.cache`` / ``await AsyncCachetic.cache()`` hand
the adapter straight to the caller, so "the client never sends that value" is not
an answer for the adapter layer.

The contract, for all eight adapters:

* ``ex is None`` — store without a deadline.
* ``ex <= 0`` — **also** store without a deadline.
* ``ex > 0`` — expire that many seconds from now.

The second line is the one that needs saying. Left to the drivers, the same call
went four ways: ``diskcache`` accepted ``expire=0`` and stored a value that was
already expired, ``redis`` raised ``invalid expire time in 'set' command``, and
MongoDB and PostgreSQL stored it forever. Rounding non-positive up to "no
deadline" follows the two that already agreed, and is the only one of the three
that neither loses the value nor raises.

Note this covers ``-1`` as much as ``0``: -1 is the default ``default_ttl``, and
it only ever reached a driver as ``None`` because ``CacheticBase._ttl_to_expiry``
translated it one layer up.

Going through the client, an effective TTL of 0 never gets this far — ``set``
drops the write instead, which is what [Principle 3](../../docs/PRINCIPLES.md)
requires and is a different thing from what this module decides.
"""

import math
import time

__all__ = ["deadline", "expiry_seconds"]


def expiry_seconds(ex: int | None) -> int | None:
    """Returns the TTL a driver that enforces deadlines itself should be given.

    ``None`` means "no deadline" to both ``diskcache`` (``expire=``) and
    ``redis`` (``ex=``), so non-positive collapses onto it.
    """
    if ex is None or ex <= 0:
        return None
    return ex


def deadline(ex: int | None) -> int | None:
    """Returns the absolute whole-second deadline MongoDB and PostgreSQL store.

    Whole seconds from a truncated clock, compared later with a strict ``<``, so
    an entry can outlive its TTL by up to a second and never expires early. See
    ``CacheticBase._ttl_to_expiry`` for why that is accepted.
    """
    if ex is None or ex <= 0:
        return None
    return int(time.time()) + math.ceil(ex)
