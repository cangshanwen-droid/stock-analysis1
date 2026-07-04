from dataclasses import dataclass
from typing import Any

from .db import execute, fetchall, fetchone, is_postgres, row_dict

MAX_ORDER_SHARES = 1_000_000


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
        execute(conn, "UPDATE users SET balance=balance-? WHERE username=?", (amount, username))
        execute(conn, "UPDATE users SET balance=balance+? WHERE username=?", (amount, sell_order["username"]))
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

    cost = price * remaining
    # balance check always passes here: place_order caps order_shares to fit balance
    execute(conn, "UPDATE users SET balance=balance-? WHERE username=?", (cost, username))
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
        execute(conn, "UPDATE users SET balance=balance-? WHERE username=?", (amount, buy_order["username"]))
        execute(conn, "UPDATE users SET balance=balance+? WHERE username=?", (amount, username))
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
    amount = price * remaining
    execute(conn, "UPDATE users SET balance=balance+? WHERE username=?", (amount, username))
    execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)",
            (username, symbol, price, remaining, round_no))
    execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)",
            ("[系统]", symbol, price, remaining, round_no))
    return TradeResult(True, f"成交 {stock_name} {remaining} 股 @ {price:.2f}", 0, round_no)


def place_order(conn, username: str, symbol: str, side: str, price: float, shares: int) -> TradeResult:
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
    # Serialize concurrent orders for the same stock via PostgreSQL advisory lock
    if is_postgres():
        lock_id = hash(symbol) & 0x7FFFFFFF
        execute(conn, "SELECT pg_advisory_xact_lock(%s)", (lock_id,))
    if side == "buy":
        user_row = row_dict(fetchone(conn, "SELECT balance FROM users WHERE username=?", (username,)))
        if not user_row:
            return TradeResult(False, "用户不存在")
        balance = float(user_row["balance"] or 0)
        order_shares = shares
        if balance < price * shares:
            order_shares = int(balance / price)
            if order_shares <= 0:
                return TradeResult(False, f"余额不足，当前最多可买 0 股")
        result = _match_buy(conn, username, symbol, price, order_shares, round_no, str(stock["name"]), balance)
    else:
        holding = get_holding_shares(conn, username, symbol)
        pending_sell = fetchone(conn, """
            SELECT COALESCE(SUM(shares),0) AS shares
            FROM order_book
            WHERE username=? AND stock_symbol=? AND trade_type='sell'
        """, (username, symbol))
        available = holding - int(pending_sell["shares"] or 0)
        if available < shares:
            return TradeResult(False, f"可卖不足：持仓 {holding} 股，已挂单 {int(pending_sell['shares'] or 0)} 股，可用 {available} 股")
        result = _match_sell(conn, username, symbol, price, shares, round_no, str(stock["name"]))
    log_action(conn, username, f"trade_{side}", symbol, f"round={round_no}, price={price}, shares={shares}, matched={result.matched}")
    return result
