from dataclasses import dataclass
from typing import Any

from .db import execute, fetchall, fetchone


@dataclass
class MarketResult:
    ok: bool
    message: str
    round: int = 0
    settled_stocks: int = 0
    matched_shares: int = 0


def row_get(row: Any, key: str, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def compute_price(stock: dict[str, Any]) -> float:
    prev = row_get(stock, "previous_close") or row_get(stock, "current_price") or 50
    buy_total = max(row_get(stock, "buy_total", 0), 1)
    sell_total = max(row_get(stock, "sell_total", 0), 1)
    premium_factor = 1 + 0.2 * (row_get(stock, "premium_rate", 50) - 50) / 50
    carbon_mean = max(row_get(stock, "industry_carbon_mean", 50), 1)
    carbon_factor = 1 - 0.5 * (row_get(stock, "carbon_price", 50) - carbon_mean) / carbon_mean
    target = prev * (buy_total / sell_total) * premium_factor * carbon_factor
    return max(round(prev * 0.9, 2), min(round(prev * 1.1, 2), round(target, 2)))


def close_market(conn) -> MarketResult:
    state = fetchone(conn, "SELECT state, round FROM market_state WHERE id=1")
    if state and state["state"] == "closed":
        return MarketResult(False, "市场已经闭市", int(state["round"] or 0))
    round_no = int(row_get(state, "round", 1) or 1)
    matched_shares = 0

    for stock in fetchall(conn, "SELECT symbol FROM stocks WHERE is_deleted=0"):
        symbol = stock["symbol"]
        buys = fetchall(conn, """
            SELECT id,username,price,shares
            FROM order_book
            WHERE stock_symbol=? AND trade_type='buy'
            ORDER BY price DESC, id ASC
        """, (symbol,))
        sells = fetchall(conn, """
            SELECT id,username,price,shares
            FROM order_book
            WHERE stock_symbol=? AND trade_type='sell'
            ORDER BY price ASC, id ASC
        """, (symbol,))
        if not buys or not sells:
            continue
        highest_buy = float(buys[0]["price"])
        lowest_sell = float(sells[0]["price"])
        if highest_buy < lowest_sell:
            continue
        match_price = round((highest_buy + lowest_sell) / 2, 2)
        total_buy_shares = sum(int(b["shares"]) for b in buys)
        total_sell_shares = sum(int(s["shares"]) for s in sells)
        executable = min(total_buy_shares, total_sell_shares)
        buy_ratio = executable / total_buy_shares if total_buy_shares else 0
        sell_ratio = executable / total_sell_shares if total_sell_shares else 0
        for buy in buys:
            fill = int(int(buy["shares"]) * buy_ratio)
            if fill <= 0:
                continue
            amount = round(fill * match_price, 2)
            execute(conn, "UPDATE users SET balance=balance-? WHERE username=?", (amount, buy["username"]))
            execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)",
                    (buy["username"], symbol, match_price, fill, round_no))
            matched_shares += fill
        for sell in sells:
            fill = int(int(sell["shares"]) * sell_ratio)
            if fill <= 0:
                continue
            amount = round(fill * match_price, 2)
            execute(conn, "UPDATE users SET balance=balance+? WHERE username=?", (amount, sell["username"]))
            execute(conn, "INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)",
                    (sell["username"], symbol, match_price, fill, round_no))
    execute(conn, "DELETE FROM order_book")

    settled = 0
    stocks = fetchall(conn, "SELECT * FROM stocks WHERE is_deleted=0")
    for stock in stocks:
        symbol = stock["symbol"]
        open_round = fetchone(conn, "SELECT MIN(round) AS round FROM rounds WHERE stock_symbol=? AND is_settled=0", (symbol,))
        if not open_round or not open_round["round"]:
            continue
        current_round = int(open_round["round"])
        txns = fetchall(conn, "SELECT trade_type,price,shares FROM transactions WHERE stock_symbol=? AND round=?", (symbol, current_round))
        buy_total = sum(float(t["price"]) * int(t["shares"]) for t in txns if t["trade_type"] == "buy")
        sell_total = sum(float(t["price"]) * int(t["shares"]) for t in txns if t["trade_type"] == "sell")
        buy_volume = sum(int(t["shares"]) for t in txns if t["trade_type"] == "buy")
        sell_volume = sum(int(t["shares"]) for t in txns if t["trade_type"] in ("sell", "force_close"))
        volume = max(buy_volume, sell_volume)
        trade_prices = [float(t["price"]) for t in txns if float(t["price"] or 0) > 0]
        next_price = compute_price(dict(stock, buy_total=buy_total, sell_total=sell_total))
        previous_close = float(stock["previous_close"] or stock["current_price"] or next_price)
        high = max([next_price, previous_close, *trade_prices])
        low = min([next_price, previous_close, *trade_prices])
        change_pct = round((next_price - previous_close) / previous_close * 100, 2) if previous_close else 0
        execute(conn, "DELETE FROM kline WHERE stock_symbol=? AND round=?", (symbol, current_round))
        execute(conn, """
            INSERT INTO kline(stock_symbol,round,open_price,high_price,low_price,close_price,volume,buy_total,sell_total,change_pct)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (symbol, current_round, previous_close, high, low, next_price, volume, buy_total, sell_total, change_pct))
        execute(conn, "UPDATE stocks SET previous_close=?, current_price=? WHERE symbol=?", (next_price, next_price, symbol))
        execute(conn, "UPDATE rounds SET is_settled=1 WHERE stock_symbol=? AND round=?", (symbol, current_round))
        settled += 1
    execute(conn, "UPDATE market_state SET state='closed' WHERE id=1")
    return MarketResult(True, "市场已闭市并完成结算", round_no, settled, matched_shares)


def open_market(conn) -> MarketResult:
    state = fetchone(conn, "SELECT state, round FROM market_state WHERE id=1")
    if not state:
        return MarketResult(False, "市场状态不存在")
    if state["state"] == "open":
        return MarketResult(False, "市场已经开盘", int(state["round"] or 1))
    new_round = int(state["round"] or 1) + 1
    for stock in fetchall(conn, "SELECT symbol FROM stocks WHERE is_deleted=0"):
        execute(conn, """
            INSERT INTO rounds(stock_symbol,round,is_settled)
            VALUES(?,?,0)
            ON CONFLICT DO NOTHING
        """, (stock["symbol"], new_round))
    execute(conn, "UPDATE market_state SET state='open', round=? WHERE id=1", (new_round,))
    return MarketResult(True, "市场已开盘", new_round)


def reset_to_round1(conn) -> MarketResult:
    stocks = fetchall(conn, "SELECT * FROM stocks WHERE is_deleted=0")
    execute(conn, "DELETE FROM transactions")
    execute(conn, "DELETE FROM order_book")
    execute(conn, "DELETE FROM kline")
    execute(conn, "DELETE FROM rounds")
    execute(conn, "UPDATE users SET balance=1000000 WHERE role='player'")
    for stock in stocks:
        revenue = row_get(stock, "revenue", 0) or 0
        total_shares = row_get(stock, "total_shares", 0) or 0
        industry_pe = row_get(stock, "industry_pe", 0) or 0
        if revenue > 0 and total_shares > 0 and industry_pe > 0:
            init_price = round(float(revenue) * 10000 / float(total_shares) / float(industry_pe), 2)
        else:
            init_price = round(float(row_get(stock, "current_price", 1) or 1), 2)
        execute(conn, "UPDATE stocks SET current_price=?, previous_close=? WHERE symbol=?", (init_price, init_price, stock["symbol"]))
        execute(conn, "INSERT INTO rounds(stock_symbol,round,is_settled) VALUES(?,1,0)", (stock["symbol"],))
        execute(conn, """
            INSERT INTO kline(stock_symbol,round,open_price,high_price,low_price,close_price,volume,buy_total,sell_total,change_pct)
            VALUES(?,?,?,?,?,?,?,?,?,0)
        """, (stock["symbol"], 1, init_price, init_price, init_price, init_price, 0, 0, 0))
    execute(conn, "UPDATE market_state SET state='open', round=1 WHERE id=1")
    return MarketResult(True, "已重开赛局并回到第 1 轮", 1, len(stocks), 0)
