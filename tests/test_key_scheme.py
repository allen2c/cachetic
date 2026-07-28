"""The key on the wire is `prefix:key`, on every backend, with nothing inserted.

[Principle 4](../docs/PRINCIPLES.md) exists because the scheme is not an
internal detail: change it and every value already cached is orphaned, silently,
with no error to notice.

What the suite had before this file was `get_cache_key`'s own return value
(async only) and behavioural isolation — write under prefix A, miss under prefix
B. Neither reads the key the backend actually stored, so both stay green if the
whole scheme changes at once. These tests go to the raw driver instead.
"""

import pathlib
import typing

import pydantic
import pytest

from cachetic import AsyncCachetic, Cachetic
from cachetic.aio import close_all as aio_close_all

STR_ADAPTER = pydantic.TypeAdapter(str)

PREFIX = "wire"
KEY = "the-key"
EXPECTED = f"{PREFIX}:{KEY}"


def _raw_keys_disk(url: pathlib.Path) -> set[str]:
    import diskcache

    with diskcache.Cache(str(url)) as raw:
        return {str(k) for k in raw.iterkeys()}


def _raw_keys_redis(url: str) -> set[str]:
    import redis

    raw = redis.Redis.from_url(url)
    try:
        found: typing.Any = raw.keys(f"*{KEY}*")
        return {k.decode() if isinstance(k, bytes) else str(k) for k in found}
    finally:
        raw.close()


def _raw_keys_mongo(url: str) -> set[str]:
    import pymongo

    from cachetic.extensions._url import parse_mongo_url

    parts = parse_mongo_url(url)
    raw = pymongo.MongoClient(parts.db_url)
    try:
        col = raw[parts.database][parts.collection]
        return {doc["name"] for doc in col.find({"name": {"$regex": KEY}})}
    finally:
        raw.close()


def _raw_keys_postgres(url: str) -> set[str]:
    import psycopg
    from psycopg import sql

    from cachetic.extensions._url import parse_postgres_url

    parts = parse_postgres_url(url)
    query = sql.SQL("SELECT name FROM {} WHERE name LIKE %s").format(sql.Identifier(parts.table))
    with psycopg.connect(parts.db_url) as conn:
        rows = conn.execute(query, (f"%{KEY}%",)).fetchall()
    return {row[0] for row in rows}


def raw_keys(url: str | pathlib.Path) -> set[str]:
    """Every stored key matching KEY, read straight from the driver."""
    if isinstance(url, pathlib.Path):
        return _raw_keys_disk(url)
    if url.startswith("redis"):
        return _raw_keys_redis(url)
    if url.startswith("mongo"):
        return _raw_keys_mongo(url)
    return _raw_keys_postgres(url)


class TestTheKeyOnTheWire:
    def test_sync_writes_prefix_colon_key(self, backend_url: str | pathlib.Path):
        cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix=PREFIX)
        try:
            cache.set(KEY, "value")
            assert raw_keys(backend_url) == {EXPECTED}
        finally:
            cache.delete(KEY)

    async def test_async_writes_the_same_key_as_sync(self, backend_url: str | pathlib.Path):
        """Both clients must land on one key, or they cannot read each other."""
        cache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=backend_url, prefix=PREFIX)
        try:
            await cache.set(KEY, "value")
            assert raw_keys(backend_url) == {EXPECTED}
        finally:
            await cache.delete(KEY)
            await aio_close_all()

    def test_an_empty_prefix_writes_the_bare_key(self, backend_url: str | pathlib.Path):
        """No prefix means no separator — not a leading colon."""
        cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=backend_url)
        try:
            cache.set(KEY, "value")
            assert raw_keys(backend_url) == {KEY}
        finally:
            cache.delete(KEY)


class TestTheBuilderIsTheOnlyWayIn:
    """`get_cache_key` is the single builder, and every operation uses it fully.

    `with_prefix=False` is the one way to get a second scheme out of it. Nothing
    in the library passes it, and nothing should: an operation that quietly
    dropped the prefix would read and write a different namespace from its three
    siblings, which is exactly the orphaning rule 4 names.
    """

    @pytest.mark.parametrize("client", ["sync", "async"])
    def test_every_operation_asks_for_the_prefix(self, tmp_path: pathlib.Path, client: str):
        import asyncio

        calls: list[bool] = []

        def record(original):
            def wrapper(self, key: str, *, with_prefix: bool = True) -> str:
                calls.append(with_prefix)
                return original(self, key, with_prefix=with_prefix)

            return wrapper

        from cachetic._base import CacheticBase

        original = CacheticBase.get_cache_key
        CacheticBase.get_cache_key = record(original)  # type: ignore[method-assign]
        try:
            url = tmp_path.joinpath(".cachetic")
            if client == "sync":
                cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=url, prefix=PREFIX)
                cache.set(KEY, "value")
                cache.get(KEY)
                cache.exists(KEY)
                cache.delete(KEY)
            else:

                async def scenario() -> None:
                    acache = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=url, prefix=PREFIX)
                    await acache.set(KEY, "value")
                    await acache.get(KEY)
                    await acache.exists(KEY)
                    await acache.delete(KEY)
                    await aio_close_all()

                asyncio.run(scenario())
        finally:
            CacheticBase.get_cache_key = original  # type: ignore[method-assign]

        assert len(calls) == 4, f"expected one key build per operation, got {len(calls)}"
        assert all(calls), "an operation built its key without the prefix"

    def test_the_builder_inserts_nothing_between_prefix_and_key(self, tmp_path: pathlib.Path):
        cache = Cachetic[str](object_type=STR_ADAPTER, cache_url=tmp_path.joinpath(".cachetic"), prefix=PREFIX)
        assert cache.get_cache_key(KEY) == EXPECTED

    def test_sync_and_async_build_the_same_string(self, tmp_path: pathlib.Path):
        url = tmp_path.joinpath(".cachetic")
        sync = Cachetic[str](object_type=STR_ADAPTER, cache_url=url, prefix=PREFIX)
        asyn = AsyncCachetic[str](object_type=STR_ADAPTER, cache_url=url, prefix=PREFIX)
        assert sync.get_cache_key(KEY) == asyn.get_cache_key(KEY) == EXPECTED
