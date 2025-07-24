# cachetic/types/cache_protocol.py
import typing


class CacheProtocol(typing.Protocol):
    def set(
        self, name: str, value: bytes, ex: typing.Optional[int] = None, *args, **kwargs
    ) -> None: ...

    def get(self, name: str, *args, **kwargs) -> typing.Optional[bytes]: ...

    def delete(self, name: str, *args, **kwargs) -> None: ...
