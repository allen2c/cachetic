import json
import logging
import pathlib
import pickle
import typing

import diskcache
import pydantic
import pydantic_settings
import redis

SUPPORTED_OBJECT_TYPE_VAR = typing.TypeVar(
    "SUPPORTED_OBJECT_TYPE_VAR",
    bound=typing.Union[
        bytes,
        str,
        int,
        float,
        bool,
        list,
        dict,
        pydantic.BaseModel,
        pydantic.TypeAdapter,
        object,
    ],
)


__version__ = pathlib.Path(__file__).parent.joinpath("VERSION").read_text().strip()


LOGGER_NAME = "cachetic"

logger = logging.getLogger(LOGGER_NAME)


class CacheNotFoundError(Exception):
    pass


class Cachetic(
    pydantic_settings.BaseSettings, typing.Generic[SUPPORTED_OBJECT_TYPE_VAR]
):
    model_config = pydantic_settings.SettingsConfigDict(arbitrary_types_allowed=True)

    object_type: typing.Any = pydantic.Field(
        default=typing.cast(typing.Type[SUPPORTED_OBJECT_TYPE_VAR], object)
    )

    cache_url: typing.Optional[pydantic.SecretStr] = pydantic.Field(
        default=None,
        description="The URL of the cache server.",
    )
    cache_dir: typing.Text = pydantic.Field(
        default=str(pathlib.Path("./.cache").resolve()),
        description="The directory to store the cache files.",
    )
    cache_ttl: int = pydantic.Field(
        default=-1,
        description=(
            "Cache time-to-live (seconds). "
            "-1: no expiration. "
            "0: disable cache. "
            ">0: expire after N seconds."
        ),
    )
    cache_prefix: str = pydantic.Field(
        default="",
        description="The prefix of the cache.",
    )

    # Private attributes
    _remote_cache: typing.Optional[redis.Redis] = pydantic.PrivateAttr(default=None)
    _local_cache: typing.Optional[diskcache.Cache] = pydantic.PrivateAttr(default=None)

    @property
    def remote_cache(self) -> typing.Union[redis.Redis, diskcache.Cache]:
        if self._remote_cache is None:

            if self.cache_url is None:
                raise ValueError("The 'cache_url' is required for remote cache.")

            logger.debug(
                f"Initializing remote cache from {self.cache_url.get_secret_value()}"
            )
            self._remote_cache = redis.Redis.from_url(self.cache_url.get_secret_value())

        return self._remote_cache

    @property
    def local_cache(self) -> diskcache.Cache:
        if self._local_cache is None:
            logger.info(f"Initializing local cache in {self.cache_dir}")
            self._local_cache = diskcache.Cache(self.cache_dir)
        return self._local_cache

    @property
    def cache(self) -> typing.Union[redis.Redis, diskcache.Cache]:
        if self.cache_url is None:
            return self.local_cache

        return self.remote_cache

    def get_cache_key(self, key: typing.Text, *, with_prefix: bool = True) -> str:
        return (
            f"{self.cache_prefix}:{key}" if with_prefix and self.cache_prefix else key
        )

    def get(
        self,
        key: typing.Text,
        *args,
        **kwargs,
    ) -> typing.Optional[SUPPORTED_OBJECT_TYPE_VAR]:
        _key = self.get_cache_key(key, with_prefix=True)

        logger.debug(f"Getting cache for '{_key}'")
        data = self.cache.get(_key)

        if data is None:
            return None

        # Output is a Pydantic model
        if hasattr(self.object_type, "model_validate_json") or isinstance(
            self.object_type, pydantic.BaseModel
        ):
            return self.object_type.model_validate_json(data)  # type: ignore
        # Output is a pydantic TypeAdapter
        elif isinstance(self.object_type, pydantic.TypeAdapter):
            return self.object_type.validate_json(data)  # type: ignore

        # Output is a primitive type
        elif self.object_type is bytes:
            return data  # type: ignore
        elif self.object_type is str:
            # Data retrieved should be bytes, decode to string
            if isinstance(data, bytes):
                return data.decode("utf-8")  # type: ignore
            elif isinstance(data, str):
                return data  # type: ignore
            raise TypeError(f"Expected bytes or str for string cache, got {type(data)}")
        elif self.object_type is int:
            return int(data)  # type: ignore
        elif self.object_type is float:
            return float(data)  # type: ignore
        elif self.object_type is bool:
            return False if data == b"0" else True  # type: ignore
        elif self.object_type is list:
            return json.loads(data)  # type: ignore
        elif self.object_type is dict:
            return json.loads(data)  # type: ignore

        # Output is a python object
        elif self.object_type is object:
            return pickle.loads(data)  # type: ignore

        raise ValueError(f"Unsupported object type: {self.object_type}")

    def get_or_raise(
        self,
        key: typing.Text,
        *args,
        **kwargs,
    ) -> SUPPORTED_OBJECT_TYPE_VAR:
        out = self.get(key, *args, **kwargs)
        if out is None:
            raise CacheNotFoundError(f"Cache not found for key '{key}'")
        return out

    def set(
        self,
        key: typing.Text,
        value: SUPPORTED_OBJECT_TYPE_VAR,
        ex: typing.Optional[int] = None,
        *args,
        **kwargs,
    ) -> None:
        _key = self.get_cache_key(key, with_prefix=True)

        ex = ex if ex is not None else self.cache_ttl if self.cache_ttl > 0 else None
        if ex == 0:
            return None  # No need to set cache

        # Dump value
        if hasattr(self.object_type, "model_dump_json") or isinstance(
            self.object_type, pydantic.BaseModel
        ):
            _value = self.object_type.model_dump_json(value)  # type: ignore
        elif isinstance(self.object_type, pydantic.TypeAdapter):
            _value = self.object_type.dump_json(value)  # type: ignore
        elif self.object_type is bytes:
            _value = value  # type: ignore
        elif self.object_type is str:
            # Ensure value is encoded to bytes before setting
            _value = value.encode("utf-8")  # type: ignore
        elif self.object_type is int:
            _value = value  # type: ignore
        elif self.object_type is float:
            _value = value  # type: ignore
        elif self.object_type is bool:
            _value = b"1" if value else b"0"
        elif self.object_type is list:
            _value = json.dumps(value, default=str)  # type: ignore
        elif self.object_type is dict:
            _value = json.dumps(value, default=str)  # type: ignore
        elif self.object_type is object:
            _value = pickle.dumps(value)  # type: ignore
        else:
            raise ValueError(f"Unsupported object type: {self.object_type}")

        logger.debug(f"Setting cache for '{_key}' with TTL {ex}")
        self.cache.set(_key, _value, ex)  # type: ignore
