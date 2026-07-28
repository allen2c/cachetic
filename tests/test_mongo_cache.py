import time
from pprint import pformat
from unittest.mock import patch

import pydantic
import pymongo.collection

from cachetic import Cachetic
from cachetic.extensions import _registry
from cachetic.extensions.mongodb import MongoCache


class Person(pydantic.BaseModel):
    name: str
    age: int


def test_mongo_cache_set_get(mongo_connection_string: str):
    print("\n--- Starting test_mongo_cache_set_get ---")
    # Use a test database and collection
    cache = Cachetic[Person](
        object_type=pydantic.TypeAdapter(Person),
        cache_url=mongo_connection_string,
    )
    key = "test_person"
    value = Person(name="Alice", age=30)
    ex = 2

    print(f"[GET] Before set: key='{key}'")
    result = cache.get(key)
    print(f"[GET] Result: {result}")
    assert result is None

    print(f"[SET] Setting key='{key}' with value={value} and ex={ex}")
    cache.set(key, value, ex=ex)
    print(f"[GET] After set: key='{key}'")
    result = cache.get(key)
    print(f"[GET] Result: {result}")
    assert result is not None
    assert pformat(result.model_dump()) == pformat(value.model_dump())

    print(f"[WAIT] Sleeping for {ex + 1} seconds to let cache expire...")
    time.sleep(ex + 1)

    print(f"[GET] After expiration: key='{key}'")
    result = cache.get(key)
    print(f"[GET] Result: {result}")
    assert result is None

    print(f"[SET] Setting key='{key}' with value={value} and no expiration")
    cache.set(key, value)
    print(f"[GET] After set (no expiration): key='{key}'")
    res = cache.get(key)
    print(f"[GET] Result: {res}")
    assert res is not None
    assert pformat(res.model_dump()) == pformat(value.model_dump())

    print(f"[WAIT] Sleeping for {ex + 1} seconds (should not expire)...")
    time.sleep(ex + 1)

    print(f"[GET] After waiting (should still exist): key='{key}'")
    res = cache.get(key)
    print(f"[GET] Result: {res}")
    assert res is not None
    assert pformat(res.model_dump()) == pformat(value.model_dump())

    print(f"[DELETE] Deleting key='{key}'")
    cache.delete(key)
    print(f"[GET] After delete: key='{key}'")
    res = cache.get(key)
    print(f"[GET] Result: {res}")
    assert res is None
    print("--- Finished test_mongo_cache_set_get ---\n")


# --- CAC-003: Connection reuse tests ---


def _clear_mongo_registries():
    """Closes and forgets every shared client, for test isolation."""
    _registry.close_all()


def test_same_url_shares_client(mongo_connection_string: str):
    """Same URL should reuse the same MongoClient instance."""
    _clear_mongo_registries()
    try:
        cache_a = MongoCache(mongo_connection_string)
        cache_b = MongoCache(mongo_connection_string)
        assert cache_a.client is cache_b.client
        assert _registry._registry_size() == 1
    finally:
        _clear_mongo_registries()


def test_different_url_creates_separate_clients(mongo_connection_string: str):
    """Different URLs should get different MongoClient instances."""
    _clear_mongo_registries()
    url_a = mongo_connection_string  # collection=test
    url_b = mongo_connection_string.replace("collection=test", "collection=test2")
    try:
        cache_a = MongoCache(url_a)
        cache_b = MongoCache(url_b)
        # Same host/db URL (only collection differs, which is stripped) → same client
        assert cache_a.client is cache_b.client

        # Now test with a truly different URL (different db)
        _clear_mongo_registries()
        url_c = "mongodb://localhost:27017/cachetic?collection=test"
        url_d = "mongodb://localhost:27017/cachetic_other?collection=test"
        cache_c = MongoCache(url_c)
        cache_d = MongoCache(url_d)
        assert cache_c.client is not cache_d.client
        assert _registry._registry_size() == 2
    finally:
        _clear_mongo_registries()


def test_create_index_called_once_per_collection(mongo_connection_string: str):
    """create_index should be called only once per (db, collection) pair."""
    _clear_mongo_registries()
    try:
        with patch("pymongo.collection.Collection.create_index") as mock_create_index:
            # The index is ensured on first use, not at construction.
            MongoCache(mongo_connection_string).col
            MongoCache(mongo_connection_string).col
            assert mock_create_index.call_count == 1
    finally:
        _clear_mongo_registries()


def test_create_index_called_per_different_collection(mongo_connection_string: str):
    """create_index should be called once per distinct collection."""
    _clear_mongo_registries()
    url_a = mongo_connection_string
    url_b = mongo_connection_string.replace("collection=test", "collection=test2")
    try:
        with patch("pymongo.collection.Collection.create_index") as mock_create_index:
            MongoCache(url_a).col
            MongoCache(url_b).col
            assert mock_create_index.call_count == 2
    finally:
        _clear_mongo_registries()


# --- Lazy expiry must not clobber a concurrent write ---


def test_expired_cleanup_does_not_drop_a_concurrent_write(mongo_connection_string: str):
    """A set() landing between the expiry read and its delete must survive.

    get() reads an expired document and then deletes it. If that delete matches
    on the key alone, a value written in between is silently lost.
    """
    _clear_mongo_registries()
    key = "expiry_race"
    backend = MongoCache(mongo_connection_string)
    try:
        backend.set(key, b"stale", 1)
        time.sleep(2)

        original_find_one = pymongo.collection.Collection.find_one
        rewritten = False

        def find_one_then_rewrite(self, *args, **kwargs):
            """Simulates a concurrent set() observed after the expiry read."""
            nonlocal rewritten
            doc = original_find_one(self, *args, **kwargs)
            if not rewritten and doc is not None and doc.get("ex") is not None:
                rewritten = True
                MongoCache(mongo_connection_string).set(key, b"fresh", -1)
            return doc

        with patch.object(pymongo.collection.Collection, "find_one", find_one_then_rewrite):
            assert backend.get(key) is None

        assert rewritten, "the simulated concurrent write never ran"
        assert backend.get(key) == b"fresh"
    finally:
        backend.delete(key)
        _clear_mongo_registries()
