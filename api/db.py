import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("SQLITE_DB_PATH", ROOT_DIR / "data" / "stock_analysis.db"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")
_pool = None
_pool_lock = threading.Lock()


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
                min_size=2,
                max_size=20,
                open=True,
                timeout=10,
                kwargs={"row_factory": dict_row},
            )
        except ImportError:
            _pool = None
    return _pool


def bind(sql: str) -> str:
    return sql.replace("?", "%s") if is_postgres() else sql


def connect():
    if is_postgres():
        pool = get_pool()
        if pool:
            return pool.connection()
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    if not DB_PATH.exists():
        raise DatabaseNotReady(f"Database not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetchone(conn, sql: str, params: tuple[Any, ...] = ()):
    return conn.execute(bind(sql), params).fetchone()


def fetchall(conn, sql: str, params: tuple[Any, ...] = ()):
    return conn.execute(bind(sql), params).fetchall()


def execute(conn, sql: str, params: tuple[Any, ...] = ()):
    return conn.execute(bind(sql), params)


def row_dict(row: Any | None) -> dict[str, Any] | None:
    return dict(row) if row else None
