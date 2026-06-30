import os
import sqlite3
from pathlib import Path

import psycopg


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE = ROOT_DIR / "data" / "stock_analysis.db"
SCHEMA_PATH = Path(__file__).with_name("schema.postgres.sql")

TABLES = [
    "users",
    "stocks",
    "transactions",
    "kline",
    "rounds",
    "market_state",
    "audit_logs",
    "login_attempts",
    "order_book",
]
ID_TABLES = [t for t in TABLES if t != "rounds"]


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


def copy_table(src: sqlite3.Connection, dst: psycopg.Connection, table: str) -> int:
    cols = sqlite_columns(src, table)
    if not cols:
        return 0
    rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
    if not rows:
        return 0
    placeholders = ", ".join(["%s"] * len(cols))
    quoted_cols = ", ".join(cols)
    update_cols = [c for c in cols if c != "id"]
    conflict = ""
    if "id" in cols:
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
        conflict = f" ON CONFLICT (id) DO UPDATE SET {updates}" if updates else " ON CONFLICT (id) DO NOTHING"
    elif table == "rounds":
        conflict = " ON CONFLICT (stock_symbol, round) DO UPDATE SET is_settled=EXCLUDED.is_settled"
    sql = f"INSERT INTO {table} ({quoted_cols}) VALUES ({placeholders}){conflict}"
    with dst.cursor() as cur:
        cur.executemany(sql, [tuple(row[c] for c in cols) for row in rows])
    return len(rows)


def reset_sequence(conn: psycopg.Connection, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_serial_sequence(%s, 'id')
            """,
            (table,),
        )
        seq = cur.fetchone()[0]
        if not seq:
            return
        cur.execute(f"SELECT COALESCE(MAX(id), 1) FROM {table}")
        max_id = cur.fetchone()[0]
        cur.execute("SELECT setval(%s, %s, true)", (seq, max_id))


def main() -> None:
    sqlite_path = Path(os.environ.get("SQLITE_DB_PATH", DEFAULT_SQLITE))
    database_url = os.environ.get("DATABASE_URL")
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_path}")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    with psycopg.connect(database_url) as dst:
        with dst.cursor() as cur:
            cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        copied = {}
        for table in TABLES:
            copied[table] = copy_table(src, dst, table)
        for table in ID_TABLES:
            reset_sequence(dst, table)
        dst.commit()
    src.close()

    for table, count in copied.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
