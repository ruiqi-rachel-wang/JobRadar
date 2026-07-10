"""Small DB compatibility layer for local SQLite and deployed Postgres."""
import os
import sqlite3
import time as _time
from contextlib import contextmanager

from .common import AUTH_DB_FILE, BASE_DIR, DB_FILE

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
    attempts = int(os.environ.get("JOBRADAR_SQLITE_LOCK_RETRY_ATTEMPTS", "30"))
    max_delay = float(os.environ.get("JOBRADAR_SQLITE_LOCK_RETRY_MAX_DELAY_SECONDS", "2.0"))
    for attempt in range(attempts):
        try:
            return fn(sql, params)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() and attempt < attempts - 1:
                _time.sleep(delay)
                delay = min(delay * 1.6, max_delay)
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


@contextmanager
def connect_auth_db(rows=False):
    """Use a separate SQLite database for account-related writes."""
    if IS_POSTGRES:
        with connect_db(rows=rows) as conn:
            yield conn
        return

    raw = sqlite3.connect(AUTH_DB_FILE, timeout=30)
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


def init_auth_db():
    """Create the account store and preserve rows from the legacy jobs DB."""
    if IS_POSTGRES:
        with connect_auth_db() as conn:
            for column_sql in (
                "plan_status TEXT DEFAULT 'free'",
                "plan_started_at TIMESTAMPTZ",
                "plan_current_period_end TIMESTAMPTZ",
                "stripe_customer_id TEXT DEFAULT ''",
                "stripe_subscription_id TEXT DEFAULT ''",
                "last_login_at TIMESTAMPTZ",
                "last_active_at TIMESTAMPTZ",
                "email_alerts_enabled INTEGER DEFAULT 1",
            ):
                add_column_if_missing(conn, "users", column_sql)
            for column_sql in (
                "email_alert_enabled INTEGER DEFAULT 0",
                "last_alert_sent_at TEXT DEFAULT ''",
            ):
                add_column_if_missing(conn, "user_saved_searches", column_sql)
        return

    with connect_auth_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                email         TEXT    DEFAULT '',
                password_hash TEXT    NOT NULL,
                role          TEXT    DEFAULT 'user',
                plan_status   TEXT    DEFAULT 'free',
                plan_started_at TEXT  DEFAULT '',
                plan_current_period_end TEXT DEFAULT '',
                stripe_customer_id TEXT DEFAULT '',
                stripe_subscription_id TEXT DEFAULT '',
                last_login_at TEXT DEFAULT '',
                last_active_at TEXT DEFAULT '',
                email_alerts_enabled INTEGER DEFAULT 1,
                created_at    TEXT    DEFAULT (datetime('now'))
            )
        """)
        for column_sql in (
            "plan_status TEXT DEFAULT 'free'",
            "plan_started_at TEXT DEFAULT ''",
            "plan_current_period_end TEXT DEFAULT ''",
            "stripe_customer_id TEXT DEFAULT ''",
            "stripe_subscription_id TEXT DEFAULT ''",
            "last_login_at TEXT DEFAULT ''",
            "last_active_at TEXT DEFAULT ''",
            "email_alerts_enabled INTEGER DEFAULT 1",
        ):
            add_column_if_missing(conn, "users", column_sql)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_apply_list (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                job_id        TEXT    NOT NULL,
                stage         TEXT    DEFAULT 'to-apply',
                added_at      TEXT    DEFAULT (datetime('now')),
                job_title     TEXT    DEFAULT '',
                job_company   TEXT    DEFAULT '',
                job_location  TEXT    DEFAULT '',
                job_url       TEXT    DEFAULT '',
                job_exp_label TEXT    DEFAULT '',
                job_posted    TEXT    DEFAULT '',
                UNIQUE(user_id, job_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_saved_searches (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                name       TEXT    NOT NULL,
                kw         TEXT    DEFAULT '',
                loc        TEXT    DEFAULT '',
                exp_yrs    TEXT    DEFAULT '',
                since      TEXT    DEFAULT 'any',
                category   TEXT    DEFAULT 'All',
                email_alert_enabled INTEGER DEFAULT 0,
                last_alert_sent_at TEXT DEFAULT '',
                created_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        for column_sql in (
            "email_alert_enabled INTEGER DEFAULT 0",
            "last_alert_sent_at TEXT DEFAULT ''",
        ):
            add_column_if_missing(conn, "user_saved_searches", column_sql)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_apply_user ON user_apply_list(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_searches_user ON user_saved_searches(user_id)")

    with connect_db(rows=True) as source:
        users = source.execute(
            "SELECT id, username, email, password_hash, role, created_at FROM users"
        ).fetchall()
        apply_rows = source.execute(
            "SELECT id, user_id, job_id, stage, added_at, job_title, job_company, "
            "job_location, job_url, job_exp_label, job_posted FROM user_apply_list"
        ).fetchall()
        searches = source.execute(
            "SELECT id, user_id, name, kw, loc, exp_yrs, since, category, created_at "
            "FROM user_saved_searches"
        ).fetchall()

    with connect_auth_db() as target:
        target.executemany(
            "INSERT OR IGNORE INTO users "
            "(id, username, email, password_hash, role, created_at) VALUES (?,?,?,?,?,?)",
            [tuple(row) for row in users],
        )
        target.executemany(
            "INSERT OR IGNORE INTO user_apply_list "
            "(id, user_id, job_id, stage, added_at, job_title, job_company, "
            "job_location, job_url, job_exp_label, job_posted) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [tuple(row) for row in apply_rows],
        )
        target.executemany(
            "INSERT OR IGNORE INTO user_saved_searches "
            "(id, user_id, name, kw, loc, exp_yrs, since, category, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [tuple(row) for row in searches],
        )


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
