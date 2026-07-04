CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    role TEXT DEFAULT 'player',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active',
    balance DOUBLE PRECISION DEFAULT 1000000
);

CREATE TABLE IF NOT EXISTS stocks (
    id BIGSERIAL PRIMARY KEY,
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
    last_update TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    stock_symbol TEXT NOT NULL,
    trade_type TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    shares INTEGER NOT NULL,
    round INTEGER DEFAULT 0,
    trade_date TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kline (
    id BIGSERIAL PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS rounds (
    stock_symbol TEXT NOT NULL,
    round INTEGER DEFAULT 0,
    is_settled INTEGER DEFAULT 0,
    PRIMARY KEY (stock_symbol, round)
);

CREATE TABLE IF NOT EXISTS market_state (
    id BIGSERIAL PRIMARY KEY,
    state TEXT DEFAULT 'open',
    round INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    attempt_time TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_book (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    stock_symbol TEXT NOT NULL,
    trade_type TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    shares INTEGER NOT NULL,
    round INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(username);
CREATE INDEX IF NOT EXISTS idx_transactions_user_stock ON transactions(username, stock_symbol);
CREATE INDEX IF NOT EXISTS idx_transactions_stock_round ON transactions(stock_symbol, round);
CREATE INDEX IF NOT EXISTS idx_kline_stock_round ON kline(stock_symbol, round);
CREATE INDEX IF NOT EXISTS idx_order_book_user ON order_book(username);
CREATE INDEX IF NOT EXISTS idx_order_book_stock_side ON order_book(stock_symbol, trade_type);
CREATE INDEX IF NOT EXISTS idx_rounds_settled ON rounds(is_settled);

INSERT INTO market_state(id, state, round)
VALUES (1, 'open', 1)
ON CONFLICT (id) DO NOTHING;
