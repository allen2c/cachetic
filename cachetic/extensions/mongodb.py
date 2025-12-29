import logging
import math
import time
import typing
import urllib.parse

import pydantic
import pymongo
from str_or_none import str_or_none

from cachetic.types.cache_protocol import CacheProtocol
from cachetic.types.document_param import DocumentParam
from cachetic.utils.hide_url_password import hide_url_password

logger = logging.getLogger(__name__)


class MongoCache(CacheProtocol):
    """A cache that uses MongoDB as a backend."""

    def __init__(self, cache_url: str):
        """Initializes the cache from a MongoDB URL.

        The URL must contain a database path and a collection query parameter.
        """
        __might_url_str = str_or_none(cache_url)
        if __might_url_str is None:
            raise ValueError(f"Invalid mongo url: {cache_url}")
        cache_url = __might_url_str

        parsed_url = urllib.parse.urlparse(cache_url)
        __safe_url = hide_url_password(str(cache_url))

        logger.debug(f"Initializing MongoCache with URL: {__safe_url}")

        if not parsed_url.scheme.startswith("mongo"):
            raise ValueError(f"Invalid mongo url: {__safe_url}")

        __db_name = str_or_none(parsed_url.path.strip("/"))
        __query_params = urllib.parse.parse_qs(parsed_url.query)
        __col_names = __query_params.pop("collection", [])
        parsed_url = parsed_url._replace(
            query=urllib.parse.urlencode(__query_params, doseq=True)
        )
        __db_url = urllib.parse.urlunparse(parsed_url)

        if __db_name is None:
            raise ValueError(
                f"Invalid mongo url: {__safe_url}, "
                + "must provide database name in path"
            )
        if len(__col_names) == 0:
            raise ValueError(
                f"Invalid mongo url: {__safe_url}, "
                + "must provide 'collection' name in query"
            )
        if len(__col_names) >= 2:
            logger.warning(
                f"Got multiple collection names in mongo url: {__safe_url}, "
                + "only the first one will be used"
            )

        __col_name = __col_names[0]
        logger.debug(
            f"Connecting to MongoDB database: {__db_name}, collection: {__col_name}"
        )
        __mongo_client = pymongo.MongoClient(__db_url, document_class=DocumentParam)
        __db = __mongo_client[__db_name]
        __col = __db[__col_name]

        __col.create_index("name", unique=True)
        logger.debug(f"Ensured unique index on 'name' in collection: {__col_name}")

        self.cache_url = pydantic.SecretStr(__db_url)
        self.client = __mongo_client
        self.db = __db
        self.col = __col

    def set(
        self, name: str, value: bytes, ex: typing.Optional[int] = None, *args, **kwargs
    ) -> None:
        """Sets a key-value pair, with an optional expiration in seconds."""
        _ex = None if ex is None or ex < 1 else math.ceil(ex)
        if _ex is not None:
            _ex = int(time.time()) + _ex

        logger.debug(
            f"[MongoCache.set] Setting key='{name}', "
            f"value_size={len(value) if hasattr(value, '__len__') else 'unknown'}, "
            f"ex={_ex}"
        )

        self.col.update_one(
            {"name": name}, {"$set": {"value": value, "ex": _ex}}, upsert=True
        )

    def get(self, name: str, *args, **kwargs) -> typing.Optional[bytes]:
        """Retrieves a value by key.

        Returns None if the key doesn't exist or has expired.
        """
        logger.debug(f"[MongoCache.get] Getting key='{name}'")
        _doc = self.col.find_one({"name": name})

        if _doc is None:
            logger.debug(f"[MongoCache.get] Key='{name}' not found.")
            return None

        if _doc["ex"] is None:
            logger.debug(f"[MongoCache.get] Key='{name}' found (no expiration).")
            return _doc["value"]

        if _doc["ex"] < int(time.time()):
            logger.debug(
                f"[MongoCache.get] Key='{name}' expired at {_doc['ex']}, "
                f"now={int(time.time())}. Deleting."
            )
            self.col.delete_one({"name": name})
            return None

        logger.debug(f"[MongoCache.get] Key='{name}' found and valid.")
        return _doc["value"]

    def delete(self, name: str, *args, **kwargs) -> None:
        """Deletes a key-value pair from the cache."""
        logger.debug(f"[MongoCache.delete] Deleting key='{name}'")
        self.col.delete_one({"name": name})
