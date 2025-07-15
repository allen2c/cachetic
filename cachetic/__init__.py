import functools
import inspect
import logging
import pathlib
import typing
import urllib.parse

import diskcache
import pydantic
import pydantic_settings
import redis
import redis.exceptions

T = typing.TypeVar("T")

__version__ = pathlib.Path(__file__).parent.joinpath("VERSION").read_text().strip()


logger = logging.getLogger(__name__)


class CacheNotFoundError(Exception):
    pass


class Cachetic(pydantic_settings.BaseSettings, typing.Generic[T]):
    model_config = pydantic_settings.SettingsConfigDict(arbitrary_types_allowed=True)

    object_type: pydantic.TypeAdapter[T]

    cache_url: typing.Text | pathlib.Path | redis.Redis | diskcache.Cache
    default_ttl: int = pydantic.Field(
        default=-1,
        description=(
            "Cache time-to-live (seconds). "
            "-1: no expiration. "
            "0: disable cache. "
            ">0: expire after N seconds."
        ),
    )
    prefix: str = pydantic.Field(
        default="",
        description="The prefix of the cache key.",
    )

    @pydantic.model_validator(mode="after")
    def validate_ttl(self) -> typing.Self:
        self.default_ttl = _validate_ttl_value(self.default_ttl)
        return self

    @functools.cached_property
    def cache(self) -> diskcache.Cache | redis.Redis:
        if isinstance(self.cache_url, redis.Redis):
            return self.cache_url
        if isinstance(self.cache_url, diskcache.Cache):
            return self.cache_url
        if isinstance(self.cache_url, pathlib.Path):
            return diskcache.Cache(self.cache_url)
        if isinstance(self.cache_url, str):
            parsed_path = urllib.parse.urlparse(self.cache_url)
            if parsed_path.scheme == "redis":
                return redis.Redis.from_url(self.cache_url)
            return diskcache.Cache(self.cache_url)

        raise ValueError(f"Unsupported cache url: {self.cache_url}")

    @property
    def cache_url_safe(self) -> str:
        return _hide_url_password(str(self.cache_url))

    def get_cache_key(self, key: typing.Text, *, with_prefix: bool = True) -> str:
        return f"{self.prefix}:{key}" if with_prefix and self.prefix else key

    def get(
        self,
        key: typing.Text,
        *args,
        **kwargs,
    ) -> typing.Optional[T]:
        _key = self.get_cache_key(key, with_prefix=True)

        logger.debug(f"Getting cache for '{_key}'")
        data = self.cache.get(_key)

        if data is None:
            return None

        if inspect.isclass(self.object_type._type) and issubclass(
            self.object_type._type, bytes
        ):
            return self.object_type.validate_python(data)

        else:
            return self.object_type.validate_json(data)  # type: ignore

    def get_or_raise(
        self,
        key: typing.Text,
        *args,
        **kwargs,
    ) -> T:
        out = self.get(key, *args, **kwargs)
        if out is None:
            raise CacheNotFoundError(f"Cache not found for key '{key}'")
        return out

    def set(
        self,
        key: typing.Text,
        value: T,
        ex: typing.Optional[int] = None,
        *args,
        **kwargs,
    ) -> None:
        _key = self.get_cache_key(key, with_prefix=True)

        ex = _validate_ttl_value(ex if ex is not None else self.default_ttl)
        if ex == 0:
            return None  # No need to set cache
        ex_params = None if ex < 0 else ex

        # Dump value
        if inspect.isclass(self.object_type._type) and issubclass(
            self.object_type._type, bytes
        ):
            _value_bytes = typing.cast(bytes, self.object_type.validate_python(value))
        else:
            _value_bytes = self.object_type.dump_json(value)

        logger.debug(f"Setting cache for '{_key}' with TTL {ex}")
        self.cache.set(_key, _value_bytes, ex_params)


def _hide_url_password(url: str) -> str:
    parsed = urllib.parse.urlparse(url)

    # If there's a password (and/or username), rebuild netloc with masked creds
    if parsed.password is not None:
        user = parsed.username or ""
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
        # if only a password (no username),
        # parsed.username=="" → user=="" → ":***@host"
        credentials = f"{user}:***"
        netloc = f"{credentials}@{host}{port}"
    else:
        # no credentials present
        netloc = parsed.netloc

    safe_parsed = urllib.parse.ParseResult(
        scheme=parsed.scheme,
        netloc=netloc,
        path=parsed.path,
        params=parsed.params,
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return safe_parsed.geturl()


def _validate_ttl_value(ttl: int) -> int:
    if ttl < 0:
        return -1
    return ttl
