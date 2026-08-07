"""Seed a fresh local SQLite database for local_smoke.py.

Run once before the smoke test (idempotent — re-running deletes and
recreates data/stock_analysis.db from scratch):

    python local_bootstrap.py
    TOKEN_SECRET=local-test-secret ADMIN_PASSWORD=local-admin-pw \
    ENABLE_ORDER_WRITES=true ENABLE_MARKET_WRITES=true ENABLE_ADMIN_WRITES=true \
    uvicorn api.main:app --port 8001
    python local_smoke.py
"""
import base64
import hashlib
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "data" / "stock_analysis.db"

# Mirrors api/main.py make_pwd so seeded passwords pass check_pwd().
_BOOT_SALT = base64.urlsafe_b64encode(b"local-bootstrap").decode("ascii").rstrip("=")


def make_pwd(password: str) -> str:
    return f"{_BOOT_SALT}:{hashlib.sha256((password + _BOOT_SALT).encode('utf-8')).hexdigest()}"


# SQLite-adapted mirror of api/schema.postgres.sql plus the fund_accounts
# DDL from api/main.py ensure_fund_accounts_schema.
DDL = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    role TEXT DEFAULT 'player',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active',
    balance DOUBLE PRECISION DEFAULT 0
);
CREATE TABLE fund_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    initial_balance DOUBLE PRECISION DEFAULT 0,
    balance DOUBLE PRECISION DEFAULT 0,
    locked INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX idx_fund_accounts_owner_unique ON fund_accounts(owner, name);
CREATE TABLE stocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    current_price DOUBLE PRECISION DEFAULT 0,
    previous_close DOUBLE PRECISION DEFAULT 0,
    is_deleted INTEGER DEFAULT 0,
    total_shares DOUBLE PRECISION DEFAULT 10000,
    revenue DOUBLE PRECISION DEFAULT 100000,
    industry_pe DOUBLE PRECISION DEFAULT 20,
    carbon_price DOUBLE PRECISION DEFAULT 50,
    industry_carbon_mean DOUBLE PRECISION DEFAULT 50,
    premium_rate DOUBLE PRECISION DEFAULT 50,
    init_funds DOUBLE PRECISION DEFAULT 5000,
    balance DOUBLE PRECISION DEFAULT 0,
    manager TEXT DEFAULT '',
    funds_locked INTEGER DEFAULT 0,
    last_update TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    stock_symbol TEXT NOT NULL,
    trade_type TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    shares INTEGER NOT NULL,
    round INTEGER DEFAULT 0,
    trade_date TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE kline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_symbol TEXT NOT NULL,
    round INTEGER DEFAULT 0,
    open_price DOUBLE PRECISION DEFAULT 0,
    high_price DOUBLE PRECISION DEFAULT 0,
    low_price DOUBLE PRECISION DEFAULT 0,
    close_price DOUBLE PRECISION DEFAULT 0,
    volume DOUBLE PRECISION DEFAULT 0,
    buy_total DOUBLE PRECISION DEFAULT 0,
    sell_total DOUBLE PRECISION DEFAULT 0,
    change_pct DOUBLE PRECISION DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE rounds (
    stock_symbol TEXT NOT NULL,
    round INTEGER DEFAULT 0,
    is_settled INTEGER DEFAULT 0,
    PRIMARY KEY (stock_symbol, round)
);
CREATE TABLE market_state (
    id INTEGER PRIMARY KEY,
    state TEXT DEFAULT 'open',
    round INTEGER DEFAULT 1
);
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    attempt_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE order_book (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    stock_symbol TEXT NOT NULL,
    trade_type TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    shares INTEGER NOT NULL,
    round INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

# revenue*10000/total_shares/industry_pe (market_ops.stock_initial_price)
# is chosen so the initial price equals the current_price the smoke test
# assumes: S1=10, S2=20, S3=50.
STOCKS = [
    ("S1", "测试股票1", 10, 100000, 10000, 10000),
    ("S2", "测试股票2", 20, 100000, 10000, 5000),
    ("S3", "测试股票3", 50, 100000, 10000, 2000),
]


def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    conn = sqlite3.connect(DB)
    try:
        conn.executescript(DDL)
        # No personal funds: cash lives in fund accounts players create.
        for username, role in (("admin", "admin"), ("p1", "player"), ("p2", "player")):
            conn.execute(
                "INSERT INTO users(username,password,role,status,balance) VALUES(?,?,?,?,0)",
                (username, make_pwd("test123"), role, "active"),
            )
        for symbol, name, price, revenue, total_shares, industry_pe in STOCKS:
            conn.execute(
                """INSERT INTO stocks(symbol,name,current_price,previous_close,total_shares,
                   revenue,industry_pe,carbon_price,industry_carbon_mean,premium_rate,init_funds)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (symbol, name, price, price, total_shares, revenue, industry_pe,
                 50, 50, 50, 5000),
            )
            conn.execute(
                "INSERT INTO rounds(stock_symbol,round,is_settled) VALUES(?,1,0)", (symbol,))
            conn.execute(
                """INSERT INTO kline(stock_symbol,round,open_price,high_price,low_price,
                   close_price,volume,buy_total,sell_total,change_pct)
                   VALUES(?,1,?,?,?,?,0,0,0,0)""",
                (symbol, price, price, price, price),
            )
        conn.execute("INSERT INTO market_state(id,state,round) VALUES(1,'open',1)")
        conn.commit()
    finally:
        conn.close()
    print(f"Bootstrapped {DB}")


if __name__ == "__main__":
    main()
