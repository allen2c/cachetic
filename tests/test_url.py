"""Connection-URL parsing, the fourth invariant in docs/architecture.md.

One parser serves both the sync and the async adapters. It has exactly one job
beyond validation: strip Cachetic's own routing parameters and leave everything
else alone, so the driver sees the connection the caller asked for.
"""

import pytest

from cachetic.extensions._url import (
    DEFAULT_POOL_MAX_SIZE,
    DEFAULT_POOL_MIN_SIZE,
    DEFAULT_POSTGRES_TABLE,
    parse_mongo_url,
    parse_postgres_url,
)


class TestParseMongoUrl:
    def test_splits_database_and_collection(self) -> None:
        parts = parse_mongo_url("mongodb://host:27017/mydb?collection=entries")
        assert parts.database == "mydb"
        assert parts.collection == "entries"
        assert "collection=" not in parts.db_url

    def test_keeps_other_query_parameters(self) -> None:
        parts = parse_mongo_url("mongodb://host/mydb?collection=c&replicaSet=rs0&authSource=admin")
        assert "replicaSet=rs0" in parts.db_url
        assert "authSource=admin" in parts.db_url

    def test_keeps_blank_query_values(self) -> None:
        """``?option=`` is meaningful; dropping it silently changes the connection."""
        parts = parse_mongo_url("mongodb://host/mydb?collection=c&directConnection=")
        assert "directConnection=" in parts.db_url

    def test_accepts_mongodb_srv_scheme(self) -> None:
        parts = parse_mongo_url("mongodb+srv://host/mydb?collection=c")
        assert parts.db_url.startswith("mongodb+srv://")

    def test_credentials_survive_the_round_trip(self) -> None:
        parts = parse_mongo_url("mongodb://user:p%40ss@host/mydb?collection=c")
        assert "user:p%40ss@host" in parts.db_url

    def test_uses_the_first_of_several_collections(self) -> None:
        parts = parse_mongo_url("mongodb://host/mydb?collection=a&collection=b")
        assert parts.collection == "a"

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "redis://host/mydb?collection=c",
            "mongodb://host/?collection=c",
            "mongodb://host/mydb",
        ],
        ids=["empty", "blank", "wrong-scheme", "no-database", "no-collection"],
    )
    def test_rejects_unusable_urls(self, url: str) -> None:
        with pytest.raises(ValueError):
            parse_mongo_url(url)


class TestParsePostgresUrl:
    def test_splits_table_out_of_the_query(self) -> None:
        parts = parse_postgres_url("postgresql://host:5432/mydb?table=entries")
        assert parts.table == "entries"
        assert "table=" not in parts.db_url

    def test_table_defaults_when_absent(self) -> None:
        parts = parse_postgres_url("postgresql://host/mydb")
        assert parts.table == DEFAULT_POSTGRES_TABLE

    def test_keeps_libpq_options(self) -> None:
        """Anything but ``table=`` must reach psycopg untouched."""
        parts = parse_postgres_url("postgresql://u:p@host:5432/mydb?table=t&sslmode=require&connect_timeout=3")
        assert "sslmode=require" in parts.db_url
        assert "connect_timeout=3" in parts.db_url
        assert parts.db_url.startswith("postgresql://u:p@host:5432/mydb")

    def test_accepts_the_postgres_scheme_alias(self) -> None:
        parts = parse_postgres_url("postgres://host/mydb?table=t")
        assert parts.db_url.startswith("postgres://")

    def test_ipv6_host_survives(self) -> None:
        parts = parse_postgres_url("postgresql://[::1]:5432/mydb?table=t")
        assert "[::1]:5432" in parts.db_url

    def test_uses_the_first_of_several_tables(self) -> None:
        parts = parse_postgres_url("postgresql://host/mydb?table=a&table=b")
        assert parts.table == "a"

    @pytest.mark.parametrize(
        "url",
        ["", "mysql://host/mydb", "postgresql://host/"],
        ids=["empty", "wrong-scheme", "no-database"],
    )
    def test_rejects_unusable_urls(self, url: str) -> None:
        with pytest.raises(ValueError):
            parse_postgres_url(url)


class TestPostgresPoolSizing:
    """``?pool_min_size=`` / ``?pool_max_size=`` are Cachetic's, not psycopg's."""

    def test_defaults_to_one_warm_connection(self) -> None:
        parts = parse_postgres_url("postgresql://host/mydb")
        assert parts.pool_min_size == DEFAULT_POOL_MIN_SIZE == 1
        assert parts.pool_max_size == DEFAULT_POOL_MAX_SIZE

    def test_max_size_default_exceeds_min_size(self) -> None:
        """psycopg reads max_size=None as "same as min_size", serialising queries."""
        assert DEFAULT_POOL_MAX_SIZE > DEFAULT_POOL_MIN_SIZE

    def test_reads_both_sizes(self) -> None:
        parts = parse_postgres_url("postgresql://host/mydb?pool_min_size=2&pool_max_size=20")
        assert (parts.pool_min_size, parts.pool_max_size) == (2, 20)

    def test_pool_parameters_are_stripped_from_the_conninfo(self) -> None:
        """psycopg rejects query parameters it does not recognise."""
        parts = parse_postgres_url("postgresql://host/mydb?pool_min_size=2&pool_max_size=20&sslmode=require")
        assert "pool_min_size" not in parts.db_url
        assert "pool_max_size" not in parts.db_url
        assert "sslmode=require" in parts.db_url

    def test_max_size_is_raised_to_cover_an_explicit_min_size(self) -> None:
        parts = parse_postgres_url("postgresql://host/mydb?pool_min_size=32")
        assert parts.pool_max_size >= 32

    @pytest.mark.parametrize(
        "url",
        [
            "postgresql://host/mydb?pool_min_size=0",
            "postgresql://host/mydb?pool_min_size=-1",
            "postgresql://host/mydb?pool_min_size=lots",
            "postgresql://host/mydb?pool_max_size=0",
            "postgresql://host/mydb?pool_min_size=8&pool_max_size=2",
        ],
        ids=["zero", "negative", "not-a-number", "zero-max", "max-below-min"],
    )
    def test_rejects_unusable_pool_sizes(self, url: str) -> None:
        with pytest.raises(ValueError):
            parse_postgres_url(url)


def test_both_postgres_backends_use_this_parser() -> None:
    """A second implementation would rot silently; there must not be one."""
    from cachetic.extensions import _url
    from cachetic.extensions import postgres as sync_postgres
    from cachetic.extensions.aio import postgres as aio_postgres

    assert sync_postgres.parse_postgres_url is _url.parse_postgres_url
    assert aio_postgres.parse_postgres_url is _url.parse_postgres_url


def test_both_mongo_backends_use_this_parser() -> None:
    from cachetic.extensions import _url
    from cachetic.extensions import mongodb as sync_mongo
    from cachetic.extensions.aio import mongodb as aio_mongo

    assert sync_mongo.parse_mongo_url is _url.parse_mongo_url
    assert aio_mongo.parse_mongo_url is _url.parse_mongo_url
