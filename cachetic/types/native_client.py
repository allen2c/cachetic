"""What ``cache_url`` accepts besides a URL.

v0.6.0 typed the field ``Text | Path | redis.Redis | diskcache.Cache``, and
[Principle 1](../../docs/PRINCIPLES.md) keeps that call working. Naming those
two classes here is not an option: [Principle 5](../../docs/PRINCIPLES.md) says
no backend package is imported until its scheme is used, and an annotation is
enough to break that — pydantic resolves annotations when the model class is
built, which happens on ``import cachetic``.

So the union member is structural. It is deliberately loose: its job is to let a
type checker accept ``Cachetic(cache_url=redis.Redis(...))``, not to decide
whether the object is usable. That decision is
``cachetic._wrap_supplied_client``, which dispatches on the module the class came
from and raises with a reason for anything it does not handle.
"""

import typing


@typing.runtime_checkable
class NativeCacheClient(typing.Protocol):
    """A backend client the caller built and still owns.

    Two methods, one positional argument each — the widest shape that both
    ``redis.Redis`` and ``diskcache.Cache`` satisfy. ``set`` is left out on
    purpose: the two disagree on its keyword arguments (``ex=`` against
    ``expire=``), and demanding a common signature would reject both.
    """

    def get(self, key: typing.Any, /) -> typing.Any: ...

    def delete(self, key: typing.Any, /) -> typing.Any: ...
