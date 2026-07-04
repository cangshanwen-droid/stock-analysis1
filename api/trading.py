from dataclasses import dataclass
from typing import Any

from .db import execute, fetchall, fetchone, is_postgres, row_dict

MAX_ORDER_SHARES = 1_000_000
COMPANY_USER_PREFIX = "[公司:"


@dataclass
class TradeResult:
    ok: bool
    message: str
    matched: int = 0
    round: int = 0


def get_holding_shares(conn, username: str, symbol: str) -> int:
    row = fetchone(conn, """
        SELECT
            COALESCE(SUM(CASE WHEN trade_type='buy' THEN shares ELSE 0 END),0) AS bought,
            COALESCE(SUM(CASE WHEN trade_type IN('sell','force_close') THEN shares ELSE 0 END),0) AS sold
        FROM transactions
        WHERE username=? AND stock_symbol=?
    """, (username, symbol))
    return int((row["bought"] or 0) - (row["sold"] or 0)) if row else 0


def log_action(conn, actor: str, action: str, target: str = "", detail: str = "") -> None:
    execute(conn,
        "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
        (actor or "system", action, target or "", detail or ""),
    )


def _update_balance(conn, trader: str, amount: float, direction: str) -> None:
    """Update balance for a user or company account.
    direction: '-' for debit (deduct), '+' for credit (add)
    """
    if trader.startswith(COMPANY_USER_PREFIX) and trader.endswith("]"):
        sym = trader[len(COMPANY_USER_PREFIX):-1]
        execute(conn, f"UPDATE stocks SET balance=balance{direction}? WHERE symbol=?", (amount, sym))
    execute(conn, f"UPDATE users SET balance=balance{direction}? WHERE username=?", (amount, trader))


def _match_buy(conn, username: str, symbol: str, price: float, shares: int, round_no: int, stock_name: str, balance: float) -> TradeResult:
    remaining = shares
    matched = 0
    total = 0.0
    while remaining > 0:
        sell_order = row_dict(fetchone(conn, """
            SELECT id,username,price,shares
            FROM order_book
            WHERE stock_symbol=? AND trade_type='sell'
            ORDER BY price ASC,id ASC
            LIMIT 1
        """, (symbol,)))
        if not sell_order:
            break
        fill = min(remaining, int(sell_order["shares"]))
        match_price = price
        amount = round(fill * match_price, 2)
        seller_holding = get_holding_shares(conn, sell_order["username"], symbol)
        pending_sell = fetchone(conn, """
            SELECT COALESCE(SUM(shares),0) AS shares
            FROM order_book
            WHERE username=? AND stock_symbol=? AND trade_type='sell' AND id!=?
        """, (sell_order["username"], symbol, sell_order["id"]))
        if seller_holding - int(pending_sell["shares"] or 0) < fill:
            execute(conn, "DELETE FROM order_book WHERE id=?", (sell_order["id"],))
            continue
        _update_balance(conn, username, amount, "-")
        _update_balance(conn, sell_order["username"], amount, "+")
        execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)",
                (username, symbol, match_price, fill, round_no))
        execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)",
                (sell_order["username"], symbol, match_price, fill, round_no))
        matched += fill
        total += amount
        remaining -= fill
        next_shares = int(sell_order["shares"]) - fill
        if next_shares <= 0:
            execute(conn, "DELETE FROM order_book WHERE id=?", (sell_order["id"],))
        else:
            execute(conn, "UPDATE order_book SET shares=? WHERE id=?", (next_shares, sell_order["id"]))
    if matched and remaining:
        execute(conn, "INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)",
                (username, symbol, price, remaining, round_no))
        return TradeResult(True, f"成交 {matched} 股 {stock_name}，剩余 {remaining} 股已挂买单", matched, round_no)
    if matched:
        avg = total / matched
        return TradeResult(True, f"全部成交 {stock_name} {matched} 股 @ {avg:.2f}", matched, round_no)

    cost = round(price * remaining, 2)
    _update_balance(conn, username, cost, "-")
    execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)",
            (username, symbol, price, remaining, round_no))
    execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)",
            ("[系统]", symbol, price, remaining, round_no))
    return TradeResult(True, f"成交 {stock_name} {remaining} 股 @ {price:.2f}", 0, round_no)


def _match_sell(conn, username: str, symbol: str, price: float, shares: int, round_no: int, stock_name: str) -> TradeResult:
    remaining = shares
    matched = 0
    total = 0.0
    while remaining > 0:
        buy_order = row_dict(fetchone(conn, """
            SELECT id,username,price,shares
            FROM order_book
            WHERE stock_symbol=? AND trade_type='buy'
            ORDER BY price DESC,id ASC
            LIMIT 1
        """, (symbol,)))
        if not buy_order:
            break
        fill = min(remaining, int(buy_order["shares"]))
        match_price = price
        amount = round(fill * match_price, 2)
        buyer = fetchone(conn, "SELECT balance FROM users WHERE username=?", (buy_order["username"],))
        if not buyer or float(buyer["balance"] or 0) < amount:
            execute(conn, "DELETE FROM order_book WHERE id=?", (buy_order["id"],))
            log_action(conn, "system", "cancel_order", buy_order["username"],
                       f"buy order for {symbol} deleted: insufficient balance (need {amount})")
            continue
        _update_balance(conn, buy_order["username"], amount, "-")
        _update_balance(conn, username, amount, "+")
        execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)",
                (buy_order["username"], symbol, match_price, fill, round_no))
        execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)",
                (username, symbol, match_price, fill, round_no))
        matched += fill
        total += amount
        remaining -= fill
        next_shares = int(buy_order["shares"]) - fill
        if next_shares <= 0:
            execute(conn, "DELETE FROM order_book WHERE id=?", (buy_order["id"],))
        else:
            execute(conn, "UPDATE order_book SET shares=? WHERE id=?", (next_shares, buy_order["id"]))
    if matched and remaining:
        execute(conn, "INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)",
                (username, symbol, price, remaining, round_no))
        return TradeResult(True, f"成交 {matched} 股 {stock_name}，剩余 {remaining} 股已挂卖单", matched, round_no)
    if matched:
        avg = total / matched
        return TradeResult(True, f"全部成交 {stock_name} {matched} 股 @ {avg:.2f}", matched, round_no)
    amount = round(price * remaining, 2)
    _update_balance(conn, username, amount, "+")
    execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)",
            (username, symbol, price, remaining, round_no))
    execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)",
            ("[系统]", symbol, price, remaining, round_no))
    return TradeResult(True, f"成交 {stock_name} {remaining} 股 @ {price:.2f}", 0, round_no)


def get_managed_companies(conn, username: str) -> list[dict[str, Any]]:
    """Return all stocks managed by username with locked funds."""
    rows = fetchall(conn,
        "SELECT symbol,balance,funds_locked FROM stocks WHERE manager=? AND is_deleted=0 ORDER BY symbol",
        (username,))
    return [row_dict(r) for r in rows]


def get_managed_company(conn, username: str) -> dict[str, Any] | None:
    """Return the first managed stock (legacy single-company support)."""
    companies = get_managed_companies(conn, username)
    return companies[0] if companies else None


def ensure_company_user(conn, stock_symbol: str, init_funds: float) -> str:
    """Create or update the company user in the users table. Returns the company username."""
    company_user = f"{COMPANY_USER_PREFIX}{stock_symbol}]"
    existing = fetchone(conn, "SELECT username,balance FROM users WHERE username=?", (company_user,))
    if not existing:
        execute(conn, "INSERT INTO users(username,role,balance,status) VALUES(?,?,?,?)",
                (company_user, "company", init_funds, "active"))
    return company_user


def place_order(conn, operator: str, symbol: str, side: str, price: float, shares: int, is_admin: bool = False, company_symbol: str | None = None) -> TradeResult:
    if shares > MAX_ORDER_SHARES:
        return TradeResult(False, f"单笔委托数量不能超过 {MAX_ORDER_SHARES} 股")
    symbol = symbol.upper()
    current_round = fetchone(conn, "SELECT MIN(round) AS round FROM rounds WHERE stock_symbol=? AND is_settled=0", (symbol,))
    round_no = int(current_round["round"] or 0) if current_round else 0
    if round_no <= 0:
        return TradeResult(False, "市场已闭市，无法交易")
    stock = row_dict(fetchone(conn, "SELECT name,current_price FROM stocks WHERE symbol=? AND is_deleted=0", (symbol,)))
    if not stock:
        return TradeResult(False, "股票不存在或已停用")
    price = round(float(stock["current_price"] or 0), 2)
    if price <= 0:
        return TradeResult(False, "股票当前价异常，无法交易")

    # Operators always trade as independent personal accounts. A managed company,
    # when provided by older clients, is only context and must not restrict symbols
    # or switch funds into a separate company account.
    company = None
    trader = operator

    # Serialize concurrent orders for the same stock via PostgreSQL advisory lock
    if is_postgres():
        lock_id = hash(symbol) & 0x7FFFFFFF
        execute(conn, "SELECT pg_advisory_xact_lock(%s)", (lock_id,))

    if side == "buy":
        if company:
            balance = float(company["balance"] or 0)
        else:
            user_row = row_dict(fetchone(conn, "SELECT balance FROM users WHERE username=?", (operator,)))
            if not user_row:
                return TradeResult(False, "用户不存在")
            balance = float(user_row["balance"] or 0)
        order_shares = shares
        if balance < price * shares:
            order_shares = int(balance / price)
            if order_shares <= 0:
                return TradeResult(False, f"余额不足，当前最多可买 0 股")
        result = _match_buy(conn, trader, symbol, price, order_shares, round_no, str(stock["name"]), balance)
    else:
        holding = get_holding_shares(conn, trader, symbol)
        pending_sell = fetchone(conn, """
            SELECT COALESCE(SUM(shares),0) AS shares
            FROM order_book
            WHERE username=? AND stock_symbol=? AND trade_type='sell'
        """, (trader, symbol))
        available = holding - int(pending_sell["shares"] or 0)
        if available < shares:
            return TradeResult(False, f"可卖不足：持仓 {holding} 股，已挂单 {int(pending_sell['shares'] or 0)} 股，可用 {available} 股")
        result = _match_sell(conn, trader, symbol, price, shares, round_no, str(stock["name"]))
    log_action(conn, operator, f"trade_{side}", symbol, f"round={round_no}, price={price}, shares={shares}, matched={result.matched}")
    return result
