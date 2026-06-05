"""Small DB compatibility layer for local SQLite and deployed Postgres."""
import os
import sqlite3
import time as _time
from contextlib import contextmanager

from .common import BASE_DIR, DB_FILE

DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if IS_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row


def placeholders(count):
    token = "%s" if IS_POSTGRES else "?"
    return ",".join(token for _ in range(count))


def adapt_sql(sql):
    return sql.replace("?", "%s") if IS_POSTGRES else sql


# SQLite permits only one writer at a time. Under the scanner's many worker
# threads plus live web traffic, a write can transiently fail with
# "database is locked" (and busy_timeout doesn't cover lock-upgrade deadlocks).
# Retry such writes briefly instead of letting one bubble up and crash the scan.
def _with_lock_retry(fn, sql, params):
    if IS_POSTGRES:
        return fn(sql, params)
    delay = 0.1
    for attempt in range(15):
        try:
            return fn(sql, params)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() and attempt < 14:
                _time.sleep(delay)
                delay = min(delay * 1.6, 1.5)
                continue
            raise


class _Connection:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        return _with_lock_retry(self.raw.execute, adapt_sql(sql), params)

    def executemany(self, sql, params_seq):
        return _with_lock_retry(self.raw.executemany, adapt_sql(sql), params_seq)

    def commit(self):
        return self.raw.commit()

    def close(self):
        return self.raw.close()


@contextmanager
def connect_db(rows=False):
    if IS_POSTGRES:
        raw = psycopg.connect(DATABASE_URL, row_factory=dict_row if rows else None)
    else:
        raw = sqlite3.connect(DB_FILE, timeout=30)
        raw.execute("PRAGMA busy_timeout=30000")
        raw.execute("PRAGMA journal_mode=WAL")
        if rows:
            raw.row_factory = sqlite3.Row
    conn = _Connection(raw)
    try:
        yield conn
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def add_column_if_missing(conn, table, column_sql):
    if IS_POSTGRES:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column_sql}")
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_sql}")
    except sqlite3.OperationalError:
        pass


def initialize_postgres_schema(conn):
    if not IS_POSTGRES:
        return
    schema = (BASE_DIR / "schema.sql").read_text(encoding="utf-8")
    for statement in schema.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)
