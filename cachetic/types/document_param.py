import typing


class DocumentParam(typing.TypedDict):
    name: typing.Required[str]
    value: typing.Required[bytes]
    ex: typing.Required[int | None]
