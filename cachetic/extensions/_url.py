"""Connection-URL parsing shared by the sync and async backends.

Cachetic URLs carry backend-specific routing in the query string — MongoDB's
``?collection=`` and PostgreSQL's ``?table=``. Those parameters are *not*
understood by the underlying drivers (``psycopg`` rejects an unknown URI query
parameter outright), so they have to be stripped before a connection is opened.

Both the sync and async adapters route through this module so the stripped URL,
and therefore the connection-registry key, is identical on both sides.
"""

import logging
import typing
import urllib.parse

from cachetic.utils.hide_url_password import hide_url_password

logger = logging.getLogger("cachetic")

DEFAULT_POSTGRES_TABLE: str = "cachetic_cache"
DEFAULT_POSTGRES_PORT: int = 5432


class MongoUrlParts(typing.NamedTuple):
    """A MongoDB cache URL split into its connection and routing pieces."""

    db_url: str
    """Connection URL with the ``collection`` parameter removed."""
    database: str
    collection: str


class PostgresUrlParts(typing.NamedTuple):
    """A PostgreSQL cache URL split into its connection and routing pieces."""

    db_url: str
    """Connection URL with the ``table`` parameter removed."""
    database: str
    table: str
    host: str | None
    port: int
    user: str
    password: str


def _strip_query_param(
    parsed: urllib.parse.ParseResult, name: str
) -> tuple[list[str], str]:
    """Removes ``name`` from the query string.

    Returns the removed values and the URL rebuilt without them.
    """
    query_params: dict[str, list[str]] = urllib.parse.parse_qs(parsed.query)
    values: list[str] = query_params.pop(name, [])
    cleaned: urllib.parse.ParseResult = parsed._replace(
        query=urllib.parse.urlencode(query_params, doseq=True)
    )
    return values, urllib.parse.urlunparse(cleaned)


def parse_mongo_url(cache_url: str) -> MongoUrlParts:
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
    collections, db_url = _strip_query_param(parsed, "collection")

    if database is None:
        raise ValueError(
            f"Invalid mongo url: {safe_url}, must provide database name in path"
        )
    if len(collections) == 0:
        raise ValueError(
            f"Invalid mongo url: {safe_url}, "
            + "must provide 'collection' name in query"
        )
    if len(collections) >= 2:
        logger.warning(
            f"Got multiple collection names in mongo url: {safe_url}, "
            + "only the first one will be used"
        )

    return MongoUrlParts(db_url=db_url, database=database, collection=collections[0])


def parse_postgres_url(cache_url: str) -> PostgresUrlParts:
    """Splits a PostgreSQL cache URL into connection details and target table.

    Raises:
        ValueError: if the URL is not a postgres URL or is missing the database
            name in its path.
    """
    parsed: urllib.parse.ParseResult = urllib.parse.urlparse(cache_url)
    safe_url: str = hide_url_password(cache_url)

    if not parsed.scheme.startswith("postgres"):
        raise ValueError(f"Invalid postgres URL: {safe_url}")

    database: str = parsed.path.strip("/")
    if not database:
        raise ValueError(
            f"Invalid postgres URL: {safe_url}, must provide database name in path"
        )

    tables, db_url = _strip_query_param(parsed, "table")
    table: str = tables[0] if tables else DEFAULT_POSTGRES_TABLE

    return PostgresUrlParts(
        db_url=db_url,
        database=database,
        table=table,
        host=parsed.hostname,
        port=parsed.port or DEFAULT_POSTGRES_PORT,
        user=urllib.parse.unquote(parsed.username or ""),
        password=urllib.parse.unquote(parsed.password or ""),
    )
