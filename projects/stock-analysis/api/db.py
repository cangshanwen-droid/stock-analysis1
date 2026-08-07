import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("SQLITE_DB_PATH", ROOT_DIR / "data" / "stock_analysis.db"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
_pool = None
_pool_lock = threading.Lock()

_LOGGER = logging.getLogger(__name__)


class DatabaseNotReady(RuntimeError):
    pass


def is_postgres() -> bool:
    return bool(DATABASE_URL)


def get_pool():
    global _pool
    if not is_postgres():
        return None
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            import psycopg_pool
            from psycopg.rows import dict_row

            _pool = psycopg_pool.ConnectionPool(
                DATABASE_URL,
                min_size=1,
                max_size=6,
                open=True,
                timeout=10,
                kwargs={"row_factory": dict_row},
            )
        except ImportError:
            _pool = None
    return _pool


def bind(sql: str) -> str:
    """Convert SQLite-style ? placeholders to PostgreSQL-style %s placeholders.

    This replacement ONLY runs when DATABASE_URL is set (PostgreSQL mode).
    In SQLite mode, the original SQL with ? placeholders is returned unchanged.

    WARNING: str.replace("?", "%s") is a naive substitution that will corrupt
    any literal ? characters embedded in parameter values (e.g., strings like
    'What is the price?' stored in the database). In practice this project
    uses ? only for parameterized query placeholders, so the risk is low.
    A production system should use the DB-API parameter style directly
    (pyformat / %s) instead of performing string-level substitution.
    """
    return sql.replace("?", "%s") if is_postgres() else sql


def connect():
    if is_postgres():
        pool = get_pool()
        import psycopg
        from psycopg.rows import dict_row

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                if pool:
                    return pool.connection()
                return psycopg.connect(DATABASE_URL, row_factory=dict_row)
            except (psycopg.OperationalError, DatabaseNotReady) as exc:
                last_exc = exc
                if attempt < 2:
                    delay = 0.5 * (2 ** attempt)
                    _LOGGER.warning(
                        "Database connection attempt %d failed (%s), retrying in %.1fs...",
                        attempt + 1, exc, delay,
                    )
                    time.sleep(delay)
        raise DatabaseNotReady(
            f"Database not ready after 3 retries: {last_exc}"
        ) from last_exc

    if not DB_PATH.exists():
        raise DatabaseNotReady(f"Database not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def keepalive() -> bool:
    """Execute a lightweight query to keep the database connection alive.

    For PostgreSQL connection pools, executes SELECT 1 to validate
    connections. Returns True on success, False on failure."""
    try:
        conn = connect()
        try:
            execute(conn, "SELECT 1")
            return True
        finally:
            conn.close()
    except Exception:
        _LOGGER.warning("Database keepalive check failed", exc_info=True)
        return False


def fetchone(conn, sql: str, params: tuple[Any, ...] = ()):
    return conn.execute(bind(sql), params).fetchone()


def fetchall(conn, sql: str, params: tuple[Any, ...] = ()):
    return conn.execute(bind(sql), params).fetchall()


def execute(conn, sql: str, params: tuple[Any, ...] = ()):
    return conn.execute(bind(sql), params)


def row_dict(row: Any | None) -> dict[str, Any] | None:
    return dict(row) if row else None
