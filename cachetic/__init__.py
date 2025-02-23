import json
import logging
import pathlib
import typing

import diskcache
import pydantic
import pydantic_settings
import redis

PydanticModelBindable = typing.TypeVar(
    "PydanticModelBindable", bound=pydantic.BaseModel
)

__version__ = pathlib.Path(__file__).parent.joinpath("VERSION").read_text().strip()


LOGGER_NAME = "cachetic"

logger = logging.getLogger(LOGGER_NAME)


class CacheNotFoundError(Exception):
    pass


class CacheticDefaultModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="allow")


class Cachetic(pydantic_settings.BaseSettings, typing.Generic[PydanticModelBindable]):
    model_config = pydantic_settings.SettingsConfigDict(arbitrary_types_allowed=True)

    object_type: typing.Type[PydanticModelBindable] = pydantic.Field(
        default=typing.cast(typing.Type[PydanticModelBindable], CacheticDefaultModel)
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
        description="The TTL of the cache. Set to -1 to disable TTL.",
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

    def get(
        self,
        key: typing.Text,
        *,
        with_prefix: bool = True,
    ) -> typing.Optional[PydanticModelBindable]:
        _key = (
            f"{self.cache_prefix}:{key}" if with_prefix and self.cache_prefix else key
        )

        logger.debug(f"Getting cache for '{_key}'")
        data = self.cache.get(_key)

        if data is None:
            return None

        return self.object_type.model_validate_json(data)  # type: ignore

    def get_objects(
        self,
        key: typing.Text,
        *,
        with_prefix: bool = True,
    ) -> typing.List[PydanticModelBindable]:
        _key = (
            f"{self.cache_prefix}:{key}" if with_prefix and self.cache_prefix else key
        )

        logger.debug(f"Getting cache for '{_key}'")
        data = self.cache.get(_key)

        if data is None:
            return []

        data_json = json.loads(data)  # type: ignore

        output: typing.List[PydanticModelBindable] = []
        for item in data_json:

            try:
                output.append(self.object_type.model_validate_json(item))

            except pydantic.ValidationError:
                _data_str = json.dumps(item, ensure_ascii=False, default=str)
                _display_data_str = (
                    _data_str[:1000] + "..." if len(_data_str) > 1000 else _data_str
                )
                logger.warning(f"Invalid JSON for '{_key}': {_display_data_str}")

        return output

    def set(
        self,
        key: typing.Text,
        value: PydanticModelBindable,
        ex: typing.Optional[int] = None,
        *,
        with_prefix: bool = True,
    ) -> None:
        _key = (
            f"{self.cache_prefix}:{key}" if with_prefix and self.cache_prefix else key
        )

        logger.debug(f"Setting cache for '{_key}' with TTL {ex}")
        self.cache.set(_key, value.model_dump_json(), ex)

    def set_objects(
        self,
        key: typing.Text,
        values: typing.List[PydanticModelBindable],
        ex: typing.Optional[int] = None,
    ) -> None:
        _key = f"{self.cache_prefix}:{key}" if self.cache_prefix else key

        logger.debug(f"Setting cache for '{_key}' with TTL {ex}")
        self.cache.set(
            _key, json.dumps([json.loads(v.model_dump_json()) for v in values]), ex
        )
