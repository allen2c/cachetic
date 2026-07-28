"""Every call v0.6.0 accepted is still accepted.

[Principle 1](../docs/PRINCIPLES.md) has two halves and only one of them has an
escape. The data half goes back to v0.1.0. The API half goes back to v0.6.0 and
is about *signatures*: a call that used to work may return a different answer,
but it may not become a ``TypeError``.

v0.6.0 declared these four methods with a trailing ``*args, **kwargs`` and threw
whatever landed there away::

    def get(self, key, *args, **kwargs)
    def get_or_raise(self, key, *args, **kwargs)
    def set(self, key, value, ex=None, *args, **kwargs)
    def delete(self, key, *args, **kwargs)

0.7.0 dropped both, which turned every one of those calls into a rejection. The
arguments are still ignored — that part was never worth keeping — but they are
ignored audibly now, and the call still runs.

``exists`` is deliberately absent: v0.6.0 had no ``exists`` at all, so there is
no floor under it and it stays strict.
"""

import pathlib
import uuid

import pydantic
import pytest

from cachetic import AsyncCachetic, Cachetic
from cachetic.aio import close_all as aio_close_all

STR_ADAPTER = pydantic.TypeAdapter(str)


def unique_key(label: str) -> str:
    return f"floor-{label}-{uuid.uuid4().hex}"


@pytest.fixture
def cache(tmp_path: pathlib.Path) -> Cachetic[str]:
    return Cachetic[str](object_type=STR_ADAPTER, cache_url=tmp_path.joinpath(".cachetic"))


@pytest.fixture
def async_cache(tmp_path: pathlib.Path) -> AsyncCachetic[str]:
    return AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=tmp_path.joinpath(".cachetic"))


class TestExtraArgumentsAreStillAccepted:
    """The calls themselves must run. What they ignore is the next class down."""

    def test_sync_get_takes_extra_positional_and_keyword_arguments(self, cache: Cachetic[str]):
        key = unique_key("get")
        cache.set(key, "stored")

        with pytest.warns(DeprecationWarning):
            assert cache.get(key, None, "leftover", legacy=True) == "stored"

    def test_sync_get_or_raise_takes_extra_arguments(self, cache: Cachetic[str]):
        key = unique_key("get-or-raise")
        cache.set(key, "stored")

        with pytest.warns(DeprecationWarning):
            assert cache.get_or_raise(key, "leftover", legacy=True) == "stored"

    def test_sync_set_takes_extra_arguments(self, cache: Cachetic[str]):
        key = unique_key("set")

        with pytest.warns(DeprecationWarning):
            cache.set(key, "stored", 3600, "leftover", legacy=True)

        assert cache.get(key) == "stored"

    def test_sync_delete_takes_extra_arguments(self, cache: Cachetic[str]):
        key = unique_key("delete")
        cache.set(key, "stored")

        with pytest.warns(DeprecationWarning):
            cache.delete(key, "leftover", legacy=True)

        assert cache.get(key) is None

    async def test_async_operations_take_extra_arguments(self, async_cache: AsyncCachetic[str]):
        key = unique_key("async")
        try:
            with pytest.warns(DeprecationWarning):
                await async_cache.set(key, "stored", 3600, "leftover", legacy=True)
            with pytest.warns(DeprecationWarning):
                assert await async_cache.get(key, None, "leftover", legacy=True) == "stored"
            with pytest.warns(DeprecationWarning):
                assert await async_cache.get_or_raise(key, "leftover") == "stored"
            with pytest.warns(DeprecationWarning):
                await async_cache.delete(key, "leftover")

            assert await async_cache.get(key) is None
        finally:
            await aio_close_all()


class TestExtraArgumentsAreIgnoredAudibly:
    """Accepting them is the rule; naming them is what stops the next silent bug.

    v0.6.0 swallowed them without a word, which is how ``cache.get("k", "x")``
    could look like ``dict.get`` and quietly return None for a whole release.
    """

    def test_the_warning_names_what_was_thrown_away(self, cache: Cachetic[str]):
        with pytest.warns(DeprecationWarning, match=r"'leftover'.*legacy=True"):
            cache.get(unique_key("named"), None, "leftover", legacy=True)

    def test_an_ordinary_call_warns_about_nothing(self, cache: Cachetic[str], recwarn: pytest.WarningsRecorder):
        key = unique_key("quiet")
        cache.set(key, "stored")
        cache.get(key)
        cache.get(key, "fallback")
        cache.get_or_raise(key)
        cache.exists(key)
        cache.delete(key)

        assert [w for w in recwarn if issubclass(w.category, DeprecationWarning)] == []

    def test_default_is_a_real_parameter_not_a_swallowed_one(self, cache: Cachetic[str]):
        """The one extra argument 0.7.0 gave a meaning to, rather than a warning."""
        assert cache.get(unique_key("missing"), "fallback") == "fallback"


class TestExistsHasNoFloor:
    """v0.6.0 had no ``exists``, so nothing has to be grandfathered into it."""

    def test_sync_exists_rejects_extra_arguments(self, cache: Cachetic[str]):
        with pytest.raises(TypeError):
            cache.exists(unique_key("exists"), "leftover")  # type: ignore[call-arg]
