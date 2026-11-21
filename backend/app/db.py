"""PostgreSQL access: a small connection pool and the database schema."""

import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://lingualink:lingualink_dev@localhost:5433/lingualink",
)

_pool = None


def pool():
    """Create the connection pool the first time it is needed."""
    global _pool

    if _pool is None:
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},  # rows behave like dictionaries
            open=True,
        )
        _pool.wait(timeout=30)

    return _pool


def close_pool():
    global _pool

    if _pool is not None:
        _pool.close()
        _pool = None


class _Connection:
    """Thin wrapper so queries can use '?' placeholders instead of psycopg's '%s'."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        return self.raw.execute(sql.replace("?", "%s"), params)


class connect:
    """Context manager that borrows a pooled connection.

    Usage:  with connect() as c:  c.execute(...)
    The transaction is committed when the block succeeds and rolled back if it raises.
    """

    def __enter__(self):
        self._context = pool().connection()
        return _Connection(self._context.__enter__())

    def __exit__(self, *exc_info):
        return self._context.__exit__(*exc_info)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    preferred_language TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages(
    id SERIAL PRIMARY KEY,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    recipient_id INTEGER NOT NULL REFERENCES users(id),
    original_text TEXT NOT NULL,
    original_language TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    attachment_name TEXT,
    attachment_path TEXT,
    attachment_mime TEXT
);

-- One translation per message and viewer, so a language change never rewrites history.
CREATE TABLE IF NOT EXISTS message_translations(
    message_id INTEGER NOT NULL REFERENCES messages(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    language TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    PRIMARY KEY(message_id, user_id)
);

CREATE TABLE IF NOT EXISTS hidden_users(
    user_id INTEGER NOT NULL,
    other_id INTEGER NOT NULL,
    PRIMARY KEY(user_id, other_id)
);

CREATE TABLE IF NOT EXISTS blocked_users(
    user_id INTEGER NOT NULL,
    other_id INTEGER NOT NULL,
    PRIMARY KEY(user_id, other_id)
);

CREATE TABLE IF NOT EXISTS chat_clears(
    user_id INTEGER NOT NULL,
    other_id INTEGER NOT NULL,
    cleared_message_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id, other_id)
);

CREATE TABLE IF NOT EXISTS message_hidden_for(
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY(message_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(sender_id, recipient_id, id);
"""


def init_db():
    """Create the tables if they do not exist yet (safe to run on every start-up)."""
    with connect() as c:
        for statement in SCHEMA.split(";"):
            if statement.strip():
                c.execute(statement)
