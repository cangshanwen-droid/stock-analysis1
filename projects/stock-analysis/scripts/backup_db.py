"""PostgreSQL backup / restore for the gipfel trading database.

Exports every table to CSV inside a timestamped zip under data/backups/.
Restore truncates the listed tables and re-imports the CSV payloads, then
resets identity sequences. The schema itself is NOT backed up — recreate
it from api/schema.postgres.sql before restoring (rate_limits is excluded:
it is transient rate-limiter state).

Usage:
    DATABASE_URL=postgresql://... python scripts/backup_db.py --backup
    DATABASE_URL=postgresql://... python scripts/backup_db.py --list
    DATABASE_URL=postgresql://... python scripts/backup_db.py --restore data/backups/backup-<ts>.zip

Note: restore is destructive — it replaces the current data with the
snapshot contents. Confirm the target database first.
"""
import argparse
import csv
import io
import os
import sys
import time
import zipfile
from pathlib import Path

import psycopg

# Runtime tables that are rebuilt on demand; never backed up or restored.
SKIP_TABLES = {"rate_limits"}

# Restore order matters only for sequence reset, not for FK integrity
# (the schema has no foreign keys); order follows backup order.
TABLES = [
    "users",
    "fund_accounts",
    "stocks",
    "transactions",
    "kline",
    "rounds",
    "market_state",
    "audit_logs",
    "login_attempts",
    "order_book",
]

BACKUP_DIR = Path(__file__).resolve().parents[1] / "data" / "backups"


def connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        sys.exit("FATAL: DATABASE_URL is not set")
    try:
        return psycopg.connect(url)
    except Exception as exc:
        sys.exit(f"FATAL: cannot connect: {exc}")


def _export_table(conn, table: str) -> tuple[str, int]:
    out = io.BytesIO()
    with conn.cursor() as cur:
        with cur.copy(f"COPY (SELECT * FROM {table}) TO STDOUT WITH (FORMAT csv, HEADER)") as copy:
            for chunk in copy:
                out.write(chunk)
    text = out.getvalue().decode("utf-8")
    row_count = max(text.count("\n") - 1, 0)  # minus header line
    return text, row_count


def _import_table(conn, table: str, csv_text: str) -> int:
    conn.execute(f"TRUNCATE {table}")
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER)") as copy:
            copy.write(csv_text)
        count = cur.rowcount
    # Reset identity sequence to max(id) so future inserts do not collide.
    id_col = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name=%s AND is_identity='YES' ORDER BY ordinal_position LIMIT 1",
        (table,),
    ).fetchone()
    if id_col:
        col = id_col[0]
        max_id = conn.execute(f"SELECT COALESCE(MAX({col}), 0) FROM {table}").fetchone()[0]
        seq = conn.execute(
            "SELECT pg_get_serial_sequence(%s, %s)", (table, col)
        ).fetchone()[0]
        if seq:
            conn.execute(f"SELECT setval(%s, %s)", (seq, max_id))
    return count


def do_backup(conn) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"backup-{stamp}.zip"
    counts: dict[str, int] = {}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in TABLES:
            csv_text, row_count = _export_table(conn, table)
            zf.writestr(f"{table}.csv", csv_text)
            counts[table] = row_count
        zf.writestr(
            "metadata.json",
            f'{{"created_at": "{stamp}", "tables": {counts}}}',
        )
    print(f"Backup written: {path}")
    for table, count in counts.items():
        print(f"  {table}: {count} rows")
    return path


def do_restore(conn, backup_path: Path) -> None:
    if not backup_path.exists():
        sys.exit(f"FATAL: backup not found: {backup_path}")
    restored: dict[str, int] = {}
    with zipfile.ZipFile(backup_path) as zf:
        for table in TABLES:
            member = f"{table}.csv"
            if member not in zf.namelist():
                print(f"  skip {table}: not in backup")
                continue
            csv_text = zf.read(member).decode("utf-8")
            count = _import_table(conn, table, csv_text)
            restored[table] = count
            print(f"  {table}: {count} rows restored")
        conn.commit()
    print(f"Restore complete from {backup_path}")
    print("NOTE: run api/schema.postgres.sql on an empty database first if restoring to a fresh DB.")


def do_list() -> None:
    if not BACKUP_DIR.exists():
        print("No backups yet.")
        return
    backups = sorted(BACKUP_DIR.glob("backup-*.zip"))
    if not backups:
        print("No backups yet.")
        return
    print(f"{len(backups)} backup(s) in {BACKUP_DIR}:")
    for b in backups:
        size = b.stat().st_size / 1024
        print(f"  {b.name}  ({size:.0f} KB)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backup", action="store_true", help="create a new snapshot")
    group.add_argument("--restore", metavar="FILE", help="restore a snapshot (destructive)")
    group.add_argument("--list", action="store_true", help="list existing snapshots")
    args = parser.parse_args()

    if args.list:
        do_list()
        return
    conn = connect()
    try:
        if args.backup:
            do_backup(conn)
        elif args.restore:
            do_restore(conn, Path(args.restore))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
