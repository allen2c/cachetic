"""Connection-URL parsing shared by the sync and async backends.

Cachetic URLs carry its own settings in the query string — MongoDB's
``?collection=``, PostgreSQL's ``?table=`` and ``?pool_min_size=`` /
``?pool_max_size=``. Those parameters are *not* understood by the underlying
drivers (``psycopg`` rejects an unknown URI query parameter outright), so they
have to be stripped before a connection is opened.

Both the sync and async adapters route through this module so the stripped URL,
and therefore the connection-registry key, is identical on both sides. Every
other query parameter is preserved and handed to the driver verbatim, which is
what keeps options such as ``sslmode`` in effect for both clients.
"""

import logging
import typing
import urllib.parse

from cachetic.utils.hide_url_password import hide_url_password

logger = logging.getLogger("cachetic")

DEFAULT_POSTGRES_TABLE: str = "cachetic_cache"

DEFAULT_POOL_MIN_SIZE: int = 1
"""Connections held open per process and URL.

A cache issues short, frequent queries, so one warm connection covers the common
case and the pool grows on demand. Anything higher multiplies across every
process in a deployment and eats into the server's connection limit for a
component that is, by definition, optional.
"""

DEFAULT_POOL_MAX_SIZE: int = 8
"""Ceiling on concurrent connections per process and URL.

Must be set explicitly: psycopg treats ``max_size=None`` as "same as min_size",
which would serialise every query through a single connection.
"""


def parse_mongo_url(cache_url: str) -> "MongoUrlParts":
    """Splits a MongoDB cache URL into connection URL, database and collection.

    Raises:
        ValueError: if the URL is empty, not a mongo URL, or is missing the
            database path or the ``collection`` query parameter.
    """
    stripped: str | None = cache_url.strip() or None
    if stripped is None:
        raise ValueError(f"Invalid mongo url: {cache_url}")
    cache_url = stripped

    parsed: urllib.parse.ParseResult = urllib.parse.urlparse(cache_url)
    safe_url: str = hide_url_password(cache_url)

    logger.debug(f"Parsing mongo URL: {safe_url}")

    if not parsed.scheme.startswith("mongo"):
        raise ValueError(f"Invalid mongo url: {safe_url}")

    database: str | None = parsed.path.strip("/") or None
    removed, db_url = _strip_query_params(parsed, "collection")
    collection: str | None = _single(removed["collection"], name="collection", safe_url=safe_url)

    if database is None:
        raise ValueError(f"Invalid mongo url: {safe_url}, must provide database name in path")
    if collection is None:
        raise ValueError(f"Invalid mongo url: {safe_url}, must provide 'collection' name in query")

    return MongoUrlParts(db_url=db_url, database=database, collection=collection)


def parse_postgres_url(cache_url: str) -> "PostgresUrlParts":
    """Splits a PostgreSQL cache URL into connection details, table and pool size.

    ``?pool_min_size=`` and ``?pool_max_size=`` size the connection pool both
    backends open. They are Cachetic's own parameters and are stripped before
    the URL reaches psycopg.

    Raises:
        ValueError: if the URL is not a postgres URL, is missing the database
            name in its path, or gives a pool size that is not a positive
            integer with ``min <= max``.
    """
    parsed: urllib.parse.ParseResult = urllib.parse.urlparse(cache_url)
    safe_url: str = hide_url_password(cache_url)

    if not parsed.scheme.startswith("postgres"):
        raise ValueError(f"Invalid postgres URL: {safe_url}")

    database: str = parsed.path.strip("/")
    if not database:
        raise ValueError(f"Invalid postgres URL: {safe_url}, must provide database name in path")

    removed, db_url = _strip_query_params(parsed, "table", "pool_min_size", "pool_max_size")

    table: str = _single(removed["table"], name="table", safe_url=safe_url) or DEFAULT_POSTGRES_TABLE
    min_size: int = _positive_int(
        _single(removed["pool_min_size"], name="pool_min_size", safe_url=safe_url),
        default=DEFAULT_POOL_MIN_SIZE,
        name="pool_min_size",
        safe_url=safe_url,
    )
    max_size: int = _positive_int(
        _single(removed["pool_max_size"], name="pool_max_size", safe_url=safe_url),
        default=max(DEFAULT_POOL_MAX_SIZE, min_size),
        name="pool_max_size",
        safe_url=safe_url,
    )
    if max_size < min_size:
        raise ValueError(
            f"Invalid postgres URL: {safe_url}, pool_max_size ({max_size}) is below pool_min_size ({min_size})"
        )

    return PostgresUrlParts(
        db_url=db_url,
        table=table,
        pool_min_size=min_size,
        pool_max_size=max_size,
    )


class MongoUrlParts(typing.NamedTuple):
    """A MongoDB cache URL split into its connection and routing pieces."""

    db_url: str
    """Connection URL with the ``collection`` parameter removed."""
    database: str
    collection: str


class PostgresUrlParts(typing.NamedTuple):
    """A PostgreSQL cache URL split into its connection and routing pieces."""

    db_url: str
    """Connection URL with Cachetic's own parameters removed.

    Passed to psycopg as a libpq conninfo string by both backends, so no part of
    it is re-parsed into individual connection arguments anywhere.
    """
    table: str
    pool_min_size: int
    pool_max_size: int


def _strip_query_params(parsed: urllib.parse.ParseResult, *names: str) -> tuple[dict[str, list[str]], str]:
    """Removes ``names`` from the query string.

    Returns the removed values by name and the URL rebuilt without them. Blank
    values are kept: ``?option=`` is meaningful to some drivers, and dropping it
    would silently change the connection.
    """
    query_params: dict[str, list[str]] = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    removed: dict[str, list[str]] = {name: query_params.pop(name, []) for name in names}
    cleaned: urllib.parse.ParseResult = parsed._replace(query=urllib.parse.urlencode(query_params, doseq=True))
    return removed, urllib.parse.urlunparse(cleaned)


def _single(values: list[str], *, name: str, safe_url: str) -> str | None:
    """Returns the first of ``values``, warning if the URL gave more than one."""
    if not values:
        return None
    if len(values) >= 2:
        logger.warning(f"Got multiple '{name}' values in url: {safe_url}, only the first one will be used")
    return values[0]


def _positive_int(value: str | None, *, default: int, name: str, safe_url: str) -> int:
    """Parses a positive integer parameter, falling back to ``default``."""
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        parsed = 0
    if parsed < 1:
        raise ValueError(f"Invalid postgres URL: {safe_url}, '{name}' must be a positive integer, got {value!r}")
    return parsed
