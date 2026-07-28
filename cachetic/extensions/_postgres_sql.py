"""SQL shared by the sync and async PostgreSQL backends.

Both backends talk to psycopg directly and compose their statements from the
templates below, so the schema and the queries cannot drift apart: there is one
``CREATE TABLE`` in the codebase, not one per client flavour. Everything is
parameterised, and the table name is injected as a quoted
:class:`psycopg.sql.Identifier` rather than interpolated.
"""

from psycopg import sql

DEFAULT_POOL_OPEN_TIMEOUT: float = 30.0
"""Seconds to wait for a pool's first live connection before giving up.

psycopg_pool retries failed connections forever in the background. Without a
bounded wait, a wrong password or an unreachable host hangs the first operation
instead of raising.
"""

CREATE_TABLE = sql.SQL(
    'CREATE TABLE IF NOT EXISTS {} ("name" TEXT NOT NULL PRIMARY KEY, ' '"value" TEXT NOT NULL, "expires_at" BIGINT)'
)

UPSERT = sql.SQL(
    "INSERT INTO {} (name, value, expires_at) VALUES (%s, %s, %s) "
    "ON CONFLICT (name) DO UPDATE "
    "SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at"
)

SELECT = sql.SQL("SELECT value, expires_at FROM {} WHERE name = %s")

DELETE = sql.SQL("DELETE FROM {} WHERE name = %s")

DELETE_EXPIRED = sql.SQL("DELETE FROM {} WHERE name = %s AND expires_at = %s")
"""Deletes an entry only if its deadline still matches the one that was read.

A plain delete-by-name would drop a value written by a concurrent ``set``
between the read that observed the expiry and the delete that acts on it.
"""
