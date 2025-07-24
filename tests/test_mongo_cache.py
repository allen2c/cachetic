import time
from pprint import pformat

import pydantic

from cachetic import Cachetic


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
