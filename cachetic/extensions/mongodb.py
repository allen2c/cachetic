import logging
import math
import time
import typing
import urllib.parse

import pydantic
import pymongo
import pymongo.collation
from str_or_none import str_or_none

from cachetic.types.cache_protocol import CacheProtocol
from cachetic.types.document_param import DocumentParam
from cachetic.utils.hide_url_password import hide_url_password

logger = logging.getLogger(__name__)


class MongoCache(CacheProtocol):
    def __init__(self, cache_url: str):
        __might_url_str = str_or_none(cache_url)
        if __might_url_str is None:
            raise ValueError(f"Invalid mongo url: {cache_url}")
        cache_url = __might_url_str

        parsed_url = urllib.parse.urlparse(cache_url)
        __safe_url = hide_url_password(str(cache_url))

        if not parsed_url.scheme.startswith("mongo"):
            raise ValueError(f"Invalid mongo url: {__safe_url}")

        __db_name = str_or_none(parsed_url.path.strip("/"))
        __query_params = urllib.parse.parse_qs(parsed_url.query)
        __col_names = __query_params.get("collection", [])
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
        __mongo_client = pymongo.MongoClient(cache_url, document_class=DocumentParam)
        __db = __mongo_client[__db_name]
        __col = __db[__col_name]

        __col.create_index("name", unique=True)

        self.cache_url = pydantic.SecretStr(cache_url)
        self.client = __mongo_client
        self.db = __db
        self.col = __col

    def set(
        self, name: str, value: bytes, ex: typing.Optional[int] = None, *args, **kwargs
    ) -> None:
        _ex = None if ex is None or ex < 1 else math.ceil(ex)
        if _ex is not None:
            _ex = int(time.time()) + _ex

        self.col.update_one(
            {"name": name}, {"$set": {"value": value, "ex": _ex}}, upsert=True
        )

    def get(self, name: str, *args, **kwargs) -> typing.Optional[bytes]:
        _doc = self.col.find_one({"name": name})

        if _doc is None:
            return None

        if _doc["ex"] is None:
            return _doc["value"]

        if _doc["ex"] < int(time.time()):
            self.col.delete_one({"name": name})
            return None

        return _doc["value"]
