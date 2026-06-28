"""
股票交易系统 — 移动端优先响应式版本
商业模拟挑战赛 · 零图标纯文字 · 触屏友好
"""
import os, sqlite3, hashlib, tempfile
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 数据库
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DB_PATH = os.path.join(tempfile.gettempdir(), "stock_analysis.db")

def get_db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON"); return conn

def hash_pwd(p): return hashlib.sha256(p.encode()).hexdigest()

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT DEFAULT 'player',created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS stocks(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT UNIQUE NOT NULL,name TEXT NOT NULL,current_price REAL DEFAULT 0,previous_close REAL DEFAULT 0,is_deleted INTEGER DEFAULT 0,total_shares REAL DEFAULT 10000,industry_pe REAL DEFAULT 20,carbon_price REAL DEFAULT 50,industry_carbon_mean REAL DEFAULT 50,premium_rate REAL DEFAULT 50,init_funds REAL DEFAULT 5000,last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT NOT NULL,stock_symbol TEXT NOT NULL,trade_type TEXT NOT NULL,price REAL NOT NULL,shares INTEGER NOT NULL,round INTEGER DEFAULT 0,trade_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS kline(id INTEGER PRIMARY KEY AUTOINCREMENT,stock_symbol TEXT NOT NULL,round INTEGER DEFAULT 0,open_price REAL DEFAULT 0,high_price REAL DEFAULT 0,low_price REAL DEFAULT 0,close_price REAL DEFAULT 0,volume REAL DEFAULT 0,buy_total REAL DEFAULT 0,sell_total REAL DEFAULT 0,change_pct REAL DEFAULT 0,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS rounds(stock_symbol TEXT NOT NULL,round INTEGER DEFAULT 0,is_settled INTEGER DEFAULT 0,PRIMARY KEY(stock_symbol,round));
        CREATE TABLE IF NOT EXISTS market_state(id INTEGER PRIMARY KEY CHECK(id=1),state TEXT DEFAULT 'open',round INTEGER DEFAULT 1);
    """)
    conn.commit()
    cur.execute("INSERT OR IGNORE INTO market_state(id,state,round) VALUES(1,'open',1)")
    # 迁移：添加用户状态列
    try: cur.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
    except: pass
    try: cur.execute("ALTER TABLE users ADD COLUMN balance REAL DEFAULT 1000000")
    except: pass
    cur.execute("UPDATE users SET status='active' WHERE status IS NULL")
    cur.execute("UPDATE users SET balance=1000000 WHERE balance IS NULL OR balance=0")
    if cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        _seed(conn)
        for s in cur.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall():
            cur.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES(?,2,0)", (s["symbol"],))
    else:
        for s in cur.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall():
            has_open = cur.execute("SELECT 1 FROM rounds WHERE stock_symbol=? AND is_settled=0", (s["symbol"],)).fetchone()
            if not has_open:
                max_r = cur.execute("SELECT COALESCE(MAX(round),0) FROM rounds WHERE stock_symbol=?", (s["symbol"],)).fetchone()[0]
                cur.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES(?,?,0)", (s["symbol"], max_r+1))
    conn.commit(); conn.close()

def _seed(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO users VALUES(1,'admin',?,'admin',datetime(),'active',1000000)", (hash_pwd("admin123"),))
    for i, u in enumerate(["player1", "player2", "player3"], 2):
        cur.execute("INSERT INTO users VALUES(?,?,?,'player',datetime(),'active',1000000)", (i, u, hash_pwd(u)))
    for sym, name, price, funds in [("TSLA", "特斯拉", 250.0, 5000), ("AAPL", "苹果", 175.0, 3500), ("NVDA", "英伟达", 450.0, 9000)]:
        cur.execute("INSERT INTO stocks(symbol,name,current_price,previous_close,init_funds) VALUES(?,?,?,?,?)", (sym, name, price, price, funds))
        cur.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES(?,1,1)", (sym,))
    trades = [("player1", "TSLA", "buy", 200.0, 100, 1), ("player1", "AAPL", "buy", 150.0, 50, 1), ("player1", "TSLA", "sell", 240.0, 80, 1), ("player2", "NVDA", "buy", 400.0, 30, 1), ("player2", "AAPL", "sell", 160.0, 40, 1), ("player3", "TSLA", "buy", 210.0, 50, 1), ("player3", "NVDA", "buy", 420.0, 20, 1)]
    for args in trades: cur.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,?,?,?,?)", args)
    conn.commit()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 价格引擎（Excel公式）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def compute_price(stock):
    prev = stock.get("previous_close") or stock.get("current_price") or 50
    bt, st_ = max(stock.get("buy_total", 0), 1), max(stock.get("sell_total", 0), 1)
    pf = 1 + 0.2 * (stock.get("premium_rate", 50) - 50) / 50
    cm = max(stock.get("industry_carbon_mean", 50), 1)
    cf = 1 - 0.5 * (stock.get("carbon_price", 50) - cm) / cm
    t = prev * (bt / st_) * pf * cf
    return max(round(prev * 0.9, 2), min(round(prev * 1.1, 2), round(t, 2)))

def settle_round(symbol):
    conn = get_db(); cur = conn.cursor()
    stock = dict(cur.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone())
    r = cur.execute("SELECT MIN(round) FROM rounds WHERE stock_symbol=? AND is_settled=0", (symbol,)).fetchone()
    cr = r[0] if r and r[0] else 0
    if cr == 0: conn.close(); return None, False, 0, 0, 0, 0, 0
    txns = cur.execute("SELECT trade_type, price, shares FROM transactions WHERE stock_symbol=? AND round=?", (symbol, cr)).fetchall()
    buys = [(t["price"], t["shares"]) for t in txns if t["trade_type"] == "buy"]
    sells = [(t["price"], t["shares"]) for t in txns if t["trade_type"] == "sell"]
    hb = max(o[0] for o in buys) if buys else 0
    ls_ = min(o[0] for o in sells) if sells else 0
    matched = hb >= ls_ if buys and sells else False
    mp = round((hb + ls_) / 2, 2) if matched else 0
    bq = sum(o[1] for o in buys); sq = sum(o[1] for o in sells)
    mv_ = min(bq, sq) if matched else 0
    bt = sum(t["price"] * t["shares"] for t in txns if t["trade_type"] == "buy")
    st_amt = sum(t["price"] * t["shares"] for t in txns if t["trade_type"] == "sell")
    tv = sum(t["shares"] for t in txns)
    pf = round(1 + 0.2 * (stock.get("premium_rate", 50) - 50) / 50, 4)
    icm = max(stock.get("industry_carbon_mean", 50), 1)
    cf = round(1 - 0.5 * (stock.get("carbon_price", 50) - icm) / icm, 4)
    np_ = compute_price(dict(stock, buy_total=bt, sell_total=st_amt))
    raw = round((stock["previous_close"] or stock["current_price"]), 2) * (bt / max(st_amt, 1)) * pf * cf
    pc = stock["previous_close"] or stock["current_price"]
    cpct = round((np_ - pc) / pc * 100, 2) if pc else 0
    hi = max(np_, pc); lo = min(np_, pc)
    cur.execute("INSERT INTO kline(stock_symbol,round,open_price,high_price,low_price,close_price,volume,buy_total,sell_total,change_pct) VALUES(?,?,?,?,?,?,?,?,?,?)", (symbol, cr, pc, hi, lo, np_, tv, bt, st_amt, cpct))
    nr = cr + 1
    cur.execute("UPDATE stocks SET previous_close=?,current_price=? WHERE symbol=?", (np_, np_, symbol))
    cur.execute("UPDATE rounds SET is_settled=1 WHERE stock_symbol=? AND round=?", (symbol, cr))
    conn.commit(); conn.close()
    return np_, matched, mp, mv_, pf, cf, round(raw, 2)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 用户 / 股票 / 持仓 / 汇总（保持原逻辑）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def auth_user(u, p):
    conn = get_db(); r = conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone(); conn.close()
    if not r or r["password"] != hash_pwd(p): return False, ""
    try:
        if r["status"] == "disabled": return False, ""
    except: pass
    return True, r["role"]

def toggle_user(username):
    conn = get_db()
    cur = conn.execute("SELECT status FROM users WHERE username=? AND role='player'", (username,)).fetchone()
    if cur: new_s = "disabled" if cur["status"] != "disabled" else "active"
    else: conn.close(); return
    conn.execute("UPDATE users SET status=? WHERE username=?", (new_s, username)); conn.commit(); conn.close()

def register_user(u, p, role="player"):
    conn = get_db()
    try:
        conn.execute("INSERT INTO users(username,password,role,balance) VALUES(?,?,?,1000000)", (u, hash_pwd(p), role))
        conn.commit(); return True, "注册成功"
    except sqlite3.IntegrityError: return False, "用户名已存在"
    finally: conn.close()

def get_all_users():
    conn = get_db(); r = conn.execute("SELECT id,username,role,created_at,status FROM users ORDER BY id").fetchall(); conn.close()
    return [dict(x) for x in r]

def reset_pwd(u, np_):
    conn = get_db(); conn.execute("UPDATE users SET password=? WHERE username=?", (hash_pwd(np_), u)); conn.commit(); conn.close()

def get_stocks():
    conn = get_db(); r = conn.execute("SELECT * FROM stocks WHERE is_deleted=0 ORDER BY symbol").fetchall(); conn.close()
    return [dict(x) for x in r]

def get_stock(sid):
    conn = get_db(); r = conn.execute("SELECT * FROM stocks WHERE id=?", (sid,)).fetchone(); conn.close()
    return dict(r) if r else None

def add_stock(sym, name, price):
    conn = get_db()
    try:
        funds = price * 10000 * 20 / 10000
        conn.execute("INSERT INTO stocks(symbol,name,current_price,previous_close,init_funds) VALUES(?,?,?,?,?)", (sym.upper(), name, price, price, funds))
        conn.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES(?,1,1)", (sym.upper(),))
        conn.commit(); return True, "添加成功"
    except sqlite3.IntegrityError: return False, "代码已存在"
    finally: conn.close()

def update_stock_params(sid, **kw):
    conn = get_db(); sets = ", ".join(f"{k}=?" for k in kw); vals = list(kw.values()) + [sid]
    conn.execute(f"UPDATE stocks SET {sets} WHERE id=?", vals); conn.commit(); conn.close()

def delete_stock(sid):
    conn = get_db(); conn.execute("UPDATE stocks SET is_deleted=1 WHERE id=?", (sid,)); conn.commit(); conn.close()

def add_trade(username, symbol, tt, price, shares):
    conn = get_db()
    r = conn.execute("SELECT MIN(round) FROM rounds WHERE stock_symbol=? AND is_settled=0", (symbol,)).fetchone()
    cr = r[0] if r and r[0] else 0
    if cr == 0: conn.close(); return False, "市场已闭市，无法交易"
    cost = price * shares
    if tt == "buy":
        bal = conn.execute("SELECT balance FROM users WHERE username=?", (username,)).fetchone()
        if not bal or bal["balance"] < cost: conn.close(); return False, "余额不足"
        conn.execute("UPDATE users SET balance=balance-? WHERE username=?", (cost, username))
    else:
        conn.execute("UPDATE users SET balance=balance+? WHERE username=?", (cost, username))
    conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,?,?,?,?)", (username, symbol, tt, price, shares, cr))
    conn.commit(); conn.close()
    return True, "交易成功"

def get_user_balance(username):
    conn = get_db(); r = conn.execute("SELECT balance FROM users WHERE username=?", (username,)).fetchone(); conn.close()
    return r["balance"] if r else 0

def is_market_open():
    conn = get_db(); r = conn.execute("SELECT state FROM market_state WHERE id=1").fetchone(); conn.close()
    return r["state"] == "open" if r else True

def get_market_round():
    conn = get_db(); r = conn.execute("SELECT round FROM market_state WHERE id=1").fetchone(); conn.close()
    return r["round"] if r else 1

def close_market():
    conn = get_db()
    r = conn.execute("SELECT state FROM market_state WHERE id=1").fetchone()
    if r and r["state"] == "closed": conn.close(); return
    stocks = conn.execute("SELECT * FROM stocks WHERE is_deleted=0").fetchall()
    for s in stocks:
        open_r = conn.execute("SELECT MIN(round) FROM rounds WHERE stock_symbol=? AND is_settled=0", (s["symbol"],)).fetchone()
        if open_r and open_r[0]:
            cr = open_r[0]
            txns = conn.execute("SELECT trade_type,price,shares FROM transactions WHERE stock_symbol=? AND round=?", (s["symbol"], cr)).fetchall()
            bt = sum(t["price"]*t["shares"] for t in txns if t["trade_type"]=="buy")
            st_amt = sum(t["price"]*t["shares"] for t in txns if t["trade_type"]=="sell")
            tv = sum(t["shares"] for t in txns)
            np_ = compute_price(dict(s, buy_total=bt, sell_total=st_amt))
            pc = s["previous_close"] or s["current_price"]
            hi = max(np_, pc); lo = min(np_, pc)
            cpct = round((np_-pc)/pc*100,2) if pc else 0
            conn.execute("INSERT INTO kline(stock_symbol,round,open_price,high_price,low_price,close_price,volume,buy_total,sell_total,change_pct) VALUES(?,?,?,?,?,?,?,?,?,?)", (s["symbol"], cr, pc, hi, lo, np_, tv, bt, st_amt, cpct))
            conn.execute("UPDATE stocks SET previous_close=?,current_price=? WHERE symbol=?", (np_, np_, s["symbol"]))
            conn.execute("UPDATE rounds SET is_settled=1 WHERE stock_symbol=? AND round=?", (s["symbol"], cr))
    conn.execute("UPDATE market_state SET state='closed' WHERE id=1")
    conn.commit(); conn.close()

def open_market():
    conn = get_db()
    r = conn.execute("SELECT state,round FROM market_state WHERE id=1").fetchone()
    if not r or r["state"] == "open": conn.close(); return
    new_round = r["round"] + 1
    stocks = conn.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall()
    for s in stocks:
        conn.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES(?,?,0)", (s["symbol"], new_round))
    conn.execute("UPDATE market_state SET state='open', round=? WHERE id=1", (new_round,))
    conn.commit(); conn.close()

def undo_market():
    """撤销上一轮：回退到闭市前状态"""
    conn = get_db()
    r = conn.execute("SELECT state,round FROM market_state WHERE id=1").fetchone()
    if not r or r["round"] <= 1: conn.close(); return
    prev_round = r["round"] - 1
    stocks = conn.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall()
    for s in stocks:
        # 删除最新轮次的k线
        conn.execute("DELETE FROM kline WHERE stock_symbol=? AND round=?", (s["symbol"], r["round"]))
        # 删除最新轮次
        conn.execute("DELETE FROM rounds WHERE stock_symbol=? AND round=?", (s["symbol"], r["round"]))
        # 恢复上一轮价格
        prev_k = conn.execute("SELECT close_price FROM kline WHERE stock_symbol=? AND round=?", (s["symbol"], prev_round)).fetchone()
        if prev_k:
            conn.execute("UPDATE stocks SET previous_close=?, current_price=? WHERE symbol=?", (prev_k["close_price"], prev_k["close_price"], s["symbol"]))
    conn.execute("UPDATE market_state SET state='open', round=? WHERE id=1", (prev_round,))
    conn.commit(); conn.close()

def reset_to_round1():
    """回到第一轮：清空所有K线和轮次，价格不变，轮次重置为1"""
    conn = get_db()
    stocks = conn.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall()
    for s in stocks:
        conn.execute("DELETE FROM kline WHERE stock_symbol=?", (s["symbol"],))
        conn.execute("DELETE FROM rounds WHERE stock_symbol=?", (s["symbol"],))
        conn.execute("INSERT INTO rounds(stock_symbol,round,is_settled) VALUES(?,1,0)", (s["symbol"],))
    conn.execute("UPDATE market_state SET state='open', round=1 WHERE id=1")
    conn.commit()
    # 验证写入
    r = conn.execute("SELECT round FROM market_state WHERE id=1").fetchone()
    actual = r["round"] if r else 1
    conn.close()
    return actual

def get_user_portfolio(username):
    conn = get_db()
    buys = conn.execute("SELECT stock_symbol,SUM(shares) s,SUM(price*shares) c FROM transactions WHERE username=? AND trade_type='buy' GROUP BY stock_symbol", (username,)).fetchall()
    sells = conn.execute("SELECT stock_symbol,SUM(shares) s FROM transactions WHERE username=? AND trade_type IN('sell','force_close') GROUP BY stock_symbol", (username,)).fetchall()
    conn.close()
    sm = {r["stock_symbol"]: r["s"] for r in sells}
    stocks = {s["symbol"]: s for s in get_stocks()}
    rows = []
    for b in buys:
        sym = b["stock_symbol"]; net = b["s"] - sm.get(sym, 0)
        if net <= 0: continue
        avg = round(b["c"] / b["s"], 2); info = stocks.get(sym, {"name": sym, "current_price": avg})
        cp = info.get("current_price", avg); mv_ = round(cp * net, 2); pnl = round((cp - avg) * net, 2)
        rows.append({"symbol": sym, "name": info["name"], "shares": int(net), "avg_cost": avg, "current_price": cp, "market_value": mv_, "pnl": pnl, "pnl_ratio": round((cp - avg) / avg * 100, 2) if avg else 0})
    return pd.DataFrame(rows)

def get_user_market_making(username):
    conn = get_db()
    rows = conn.execute("SELECT t.stock_symbol,t.price sp,t.shares,t.trade_date,COALESCE(s.current_price,t.price) cp,COALESCE(s.name,t.stock_symbol) nm FROM transactions t LEFT JOIN stocks s ON t.stock_symbol=s.symbol WHERE t.username=? AND t.trade_type='sell' ORDER BY t.trade_date DESC", (username,)).fetchall()
    conn.close()
    return pd.DataFrame([{"股票": r["nm"], "卖出价": round(r["sp"], 2), "当前价": round(r["cp"], 2), "数量": r["shares"], "对手方盈亏": round((r["cp"] - r["sp"]) * r["shares"], 2), "时间": r["trade_date"]} for r in rows])

def get_user_overview(username):
    pf = get_user_portfolio(username)
    if pf.empty: return {"total_assets": 0, "total_cost": 0, "total_pnl": 0, "pnl_ratio": 0, "stock_count": 0, "stock_pnl": []}
    ta, tc = pf["market_value"].sum(), (pf["avg_cost"] * pf["shares"]).sum()
    tp = ta - tc
    return {"total_assets": round(ta, 2), "total_cost": round(tc, 2), "total_pnl": round(tp, 2), "pnl_ratio": round(tp / tc * 100, 2) if tc else 0, "stock_count": len(pf), "stock_pnl": pf[["name", "symbol", "pnl"]].to_dict("records")}

def get_admin_summary():
    stocks = get_stocks()
    if not stocks: return pd.DataFrame()
    conn = get_db(); players = conn.execute("SELECT username FROM users WHERE role='player'").fetchall(); conn.close()
    aps = {}
    for p in players:
        df = get_user_portfolio(p["username"])
        if not df.empty: aps[p["username"]] = df
    rows = []
    for s in stocks:
        sym = s["symbol"]; ts = tc = tp = 0.0; cnt = 0
        for un, pf in aps.items():
            r = pf[pf["symbol"] == sym]
            if r.empty: continue
            rr = r.iloc[0]; ts += rr["shares"]; tc += rr["avg_cost"] * rr["shares"]; tp += rr["pnl"]; cnt += 1
        rows.append({"股票名称": s["name"], "代码": sym, "当前价": s["current_price"], "持有用户数": cnt, "总持仓量": int(ts), "总成本": round(tc, 2), "总盈亏": round(tp, 2), "收益率": round(tp / tc * 100, 2) if cnt and tc else 0})
    return pd.DataFrame(rows)

def get_holder_detail(symbol):
    conn = get_db(); players = conn.execute("SELECT username FROM users WHERE role='player'").fetchall(); conn.close()
    r = []
    for p in players:
        pf = get_user_portfolio(p["username"])
        if pf.empty: continue
        h = pf[pf["symbol"] == symbol]
        if h.empty: continue
        rr = h.iloc[0]; r.append({"用户名": p["username"], "持仓量": int(rr["shares"]), "成本价": rr["avg_cost"], "当前价": rr["current_price"], "盈亏": rr["pnl"], "收益率": rr["pnl_ratio"]})
    return pd.DataFrame(r)

def get_kline_data(symbol):
    conn = get_db(); r = conn.execute("SELECT * FROM kline WHERE stock_symbol=? ORDER BY round", (symbol,)).fetchall(); conn.close()
    return [dict(x) for x in r]

def get_platform_stats():
    s = get_admin_summary()
    if s.empty: return {"total_mv": 0, "total_pnl": 0, "active_users": 0}
    conn = get_db(); cnt = conn.execute("SELECT COUNT(*) FROM users WHERE role='player'").fetchone()[0]; conn.close()
    return {"total_mv": round((s["当前价"] * s["总持仓量"]).sum(), 2), "total_pnl": round(s["总盈亏"].sum(), 2), "active_users": cnt}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 响应式 CSS — 移动端优先
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESPONSIVE_CSS = """
<style>
*, *::before, *::after { box-sizing: border-box; }
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    -webkit-font-smoothing: antialiased;
}

/* ===== 隐藏 Streamlit 默认 UI ===== */
#MainMenu, .stDeployButton, footer, [data-testid="stStatusWidget"],
[data-testid="stDecoration"], [data-testid="stToolbar"],
[data-testid="manage-app-button"], .st-emotion-cache-1r6slb0 { display: none !important; }

/* ===== 颜色系统 ===== */
:root {
    --bg:       #f5f7fb;
    --card:     #FFFFFF;
    --text:     #111827;
    --text-2nd: #666;
    --primary:  #2D6AFF;
    --green:    #16a34a;
    --red:      #ef4444;
}

/* ===== 移动端基础 ===== */
.stApp { background: var(--bg); }
section.main > div.block-container {
    padding: 12px !important; max-width: 100% !important;
}

/* 顶栏 */
.topbar {
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 0 12px 0; font-size: 14px; color: #666;
}
.topbar .brand { font-size: 24px; font-weight: 700; color: var(--text); }

/* KPI 网格 - 移动端 2x2 */
.kpi-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
    margin-bottom: 20px;
}
.kpi-card {
    background: var(--card); border-radius: 10px; padding: 24px 20px;
    box-shadow: 0 2px 10px rgba(0,0,0,.04);
}
.kpi-card .label { font-size: 13px; color: #666; margin-bottom: 6px; }
.kpi-card .value {
    font-size: 28px; font-weight: 600; color: var(--text);
    font-feature-settings: "tnum"; font-variant-numeric: tabular-nums;
}
.kpi-card .delta { font-size: 14px; margin-top: 2px; }
.kpi-card .delta.up { color: var(--green); }
.kpi-card .delta.down { color: var(--red); }

/* 移动端股票卡片 */
.stock-card {
    background: var(--card); border-radius: 12px; padding: 16px;
    margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.04);
    border: 1px solid var(--border);
}
.stock-card .sc-header {
    display: flex; justify-content: space-between; align-items: center;
}
.stock-card .sc-name { font-size: 16px; font-weight: 600; color: var(--text); }
.stock-card .sc-pct { font-size: 15px; font-weight: 600; }
.stock-card .sc-pct.up { color: var(--green); }
.stock-card .sc-pct.down { color: var(--red); }
.stock-card .sc-detail {
    display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px;
    margin: 8px 0; font-size: 13px; color: var(--text-2nd);
}
.stock-card .sc-detail .val { color: var(--text); font-weight: 500; }
.stock-card .sc-actions { display: flex; gap: 8px; }
.sc-btn {
    flex: 1; height: 40px; border: none; border-radius: 8px;
    font-size: 14px; font-weight: 600; cursor: pointer; font-family: inherit;
    transition: transform .08s; background: var(--card); color: var(--text);
}
.sc-btn:active { transform: scale(.97); }
.sc-btn.buy { background: var(--primary); color: #fff; }
.sc-btn.sell { background: var(--bg); color: var(--text); border: 1px solid var(--border); }

/* 底部交易栏 — 移动端 */
.trade-bar {
    position: fixed; bottom: 0; left: 0; right: 0; z-index: 100;
    background: var(--card); padding: 12px 16px;
    border-top: 1px solid var(--border);
    display: flex; gap: 8px; align-items: center;
    box-shadow: 0 -2px 8px rgba(0,0,0,.04);
}
.trade-bar select, .trade-bar input {
    height: 40px; border: 1px solid var(--border); border-radius: 8px;
    padding: 0 10px; font-size: 14px; font-family: inherit; flex: 1; min-width: 0;
    background: var(--bg); color: var(--text);
}
.trade-bar button {
    height: 40px; background: var(--primary); color: #fff;
    border: none; border-radius: 8px; padding: 0 20px;
    font-weight: 600; font-size: 14px; cursor: pointer; font-family: inherit;
    transition: transform .08s;
}
.trade-bar button:active { transform: scale(.97); }
.trade-bar-spacer { height: 60px; } /* 防止固定栏遮挡内容 */

/* 桌面端可见/隐藏 */
.desktop-only { display: none; }
.mobile-only { display: block; }

/* ===== 桌面端 @media (min-width: 768px) ===== */
@media (min-width: 768px) {
    section.main > div.block-container { padding: 32px !important; }
    .kpi-grid { grid-template-columns: repeat(4, 1fr); gap: 16px; }
    .kpi-card .label { font-size: 10px; }
    .kpi-card .value { font-size: 24px; }
    .desktop-only { display: block; }
    .mobile-only { display: none; }
    .trade-bar { display: none; }
    .trade-bar-spacer { display: none; }

    /* 桌面端表格替代卡片 */
    .desktop-table {
        background: var(--card); border-radius: 12px; padding: 4px 16px 16px 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,.04); border: 1px solid var(--border);
    }
}

/* 侧边栏桌面样式 */
@media (min-width: 768px) {
    [data-testid="stSidebarNav"] { display: none !important; }
}
</style>
"""

SIDEBAR_CSS = """
<style>
    section[data-testid="stSidebar"] { background-color: #0d1117 !important; }
    section[data-testid="stSidebar"] > div:first-child { background-color: #0d1117 !important; padding: 0 !important; }
    [data-testid="stSidebarNav"] { display: none !important; }
    [data-testid="stStatusWidget"] { display: none !important; }
    .stDeployButton, footer, #MainMenu { display: none !important; }
    [data-testid="stToolbar"] { display: none !important; }
    [data-testid="stDecoration"] { display: none !important; }
    [data-testid="manage-app-button"] { display: none !important; }
    .st-emotion-cache-1r6slb0 { display: none !important; }

    /* 全部白字，仅副标题微调 */
    section[data-testid="stSidebar"] * { color: #ffffff !important; }
    section[data-testid="stSidebar"] .sb-brand .sub p { color: #8b949e !important; }
    section[data-testid="stSidebar"] .sb-user .urole p { color: #c9d1d9 !important; }

    .sb-brand { padding: 28px 20px 20px 20px; border-bottom: 1px solid #21262d; }
    .sb-brand .name { font-size: 24px; font-weight: 700; }
    .sb-user { padding: 18px 20px 20px 20px; border-bottom: 1px solid #21262d; }

    section[data-testid="stSidebar"] div[role="radiogroup"] label {
        padding: 10px 14px !important; margin: 2px 10px !important; border-radius: 6px !important;
        color: #e6edf3 !important; font-size: 15px !important; font-weight: 500 !important;
        min-height: auto !important; position: relative !important; cursor: pointer !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover { background: #21262d !important; color: #ffffff !important; }
    section[data-testid="stSidebar"] div[role="radiogroup"] [data-checked="true"] {
        background: #1f6feb22 !important; color: #58a6ff !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] [data-checked="true"]::before {
        content: ' '; position: absolute; left: 0; top: 50%; transform: translateY(-50%);
        width: 3px; height: 18px; background: #f85149; border-radius: 0 3px 3px 0;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label input { display: none !important; }
    section[data-testid="stSidebar"] div[role="radiogroup"] label div[data-testid="stMarkdownContainer"] p { margin: 0; font-size: 15px; font-weight: 500; }
</style>
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 登录页
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def page_login():
    st.markdown("""
    <style>
        .stApp { background: #f0f2f5; }
        .stApp > header { height: 0 !important; overflow: hidden; }
        div[data-testid="stToolbar"] { visibility: hidden; }
        div[data-testid="stButton"] button[kind="primary"] { border-radius: 8px !important; padding: 12px !important; font-size: 16px !important; }
        div[data-testid="stButton"] button { border-radius: 8px !important; padding: 12px !important; }
    </style>""", unsafe_allow_html=True)

    # 初始化 tab
    if "login_tab" not in st.session_state:
        st.session_state.login_tab = "login"

    left, center, right = st.columns([1, 5, 1])
    with center:
        st.markdown("<div style='height:4vh'></div>", unsafe_allow_html=True)

        # 品牌头部
        st.markdown("""
        <div style="background:linear-gradient(135deg,#0f1420,#1a2236);padding:40px 20px;border-radius:14px 14px 0 0;text-align:center;">
            <h1 style="font-size:42px;font-weight:700;color:#fff;letter-spacing:6px;margin:0;">双镜</h1>
            <div style="width:60px;height:3px;background:#3182ce;margin:12px auto;border-radius:3px;"></div>
            <p style="font-size:16px;color:#a0aec0;margin:8px 0 0 0;">智能投资分析系统</p>
        </div>""", unsafe_allow_html=True)

        # 消息显示
        if st.session_state.get("login_error"):
            st.error(st.session_state.login_error)
            st.session_state.login_error = ""
        if st.session_state.get("login_ok"):
            st.success(st.session_state.login_ok)
            st.balloons()
            st.session_state.login_ok = ""

        # 卡片体
        st.markdown('<div style="background:#fff;padding:32px 36px;border-radius:0 0 14px 14px;box-shadow:0 4px 24px rgba(0,0,0,.08);">', unsafe_allow_html=True)

        # Tab 行
        c_t, c_r = st.columns(2)
        with c_t:
            t = "primary" if st.session_state.login_tab == "login" else "secondary"
            if st.button("登录", key="tab_l", type=t, use_container_width=True):
                st.session_state.login_tab = "login"; st.rerun()
        with c_r:
            t = "primary" if st.session_state.login_tab == "register" else "secondary"
            if st.button("注册", key="tab_r", type=t, use_container_width=True):
                st.session_state.login_tab = "register"; st.rerun()

        # 表单
        if st.session_state.login_tab == "login":
            with st.form("login_form"):
                st.text_input("用户名", placeholder="请输入用户名", label_visibility="collapsed", key="login_u")
                st.text_input("密码", type="password", placeholder="请输入密码", label_visibility="collapsed", key="login_p")
                if st.form_submit_button("登录", type="primary", use_container_width=True):
                    u = st.session_state.get("login_u", ""); p = st.session_state.get("login_p", "")
                    if not u or not p: st.session_state.login_error = "请输入用户名和密码"
                    else:
                        ok, role = auth_user(u, p)
                        if ok: st.session_state.logged_in = True; st.session_state.username = u; st.session_state.role = role
                        else: st.session_state.login_error = "用户名或密码错误"
                    st.rerun()
        else:
            with st.form("register_form"):
                st.text_input("用户名", placeholder="至少3位", label_visibility="collapsed", key="reg_u")
                st.text_input("密码", type="password", placeholder="至少4位", label_visibility="collapsed", key="reg_p")
                st.text_input("确认密码", type="password", placeholder="再次输入", label_visibility="collapsed", key="reg_p2")
                if st.form_submit_button("立即注册", type="primary", use_container_width=True):
                    u2 = st.session_state.get("reg_u", ""); p2 = st.session_state.get("reg_p", ""); p3 = st.session_state.get("reg_p2", "")
                    if not u2 or not p2: st.session_state.login_error = "请完整填写"
                    elif len(u2) < 3: st.session_state.login_error = "用户名至少3位"
                    elif len(p2) < 4: st.session_state.login_error = "密码至少4位"
                    elif p2 != p3: st.session_state.login_error = "两次密码不一致"
                    else:
                        ok, msg = register_user(u2, p2)
                        if ok: st.session_state.login_ok = "注册成功，请登录"; st.session_state.login_tab = "login"
                        else: st.session_state.login_error = msg
                    st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<p style="text-align:center;color:#a0aec0;font-size:12px;margin-top:16px;">(c) 2026</p>', unsafe_allow_html=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 通用组件
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def kpi_card(label, value, delta=None, up=True):
    d = f'<div class="delta {"up" if up else "down"}">{delta}</div>' if delta else ""
    return f'<div class="kpi-card"><div class="label">{label}</div><div class="value">{value}</div>{d}</div>'

def fmt_money(v):   return f"¥{v:,.0f}"
def fmt_pnl(v):     return f"¥{v:,.2f}"
def fmt_pct(v, s=True):
    sign = "+" if (s and v > 0) else ("" if s else "")
    return f"{sign}{v:,.2f}%"

def fmt_num(v):     return f"{v:,}"

GREEN = "#16a34a"; RED = "#ef4444"

def pnl_class(v): return "up" if v >= 0 else "down"
def pnl_color(v): return GREEN if v >= 0 else RED

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 页面：总览
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def page_overview():
    data = get_user_overview(st.session_state.username)
    bal = get_user_balance(st.session_state.username)

    # 顶栏：品牌左 / 用户名+更新时间右
    c1, c2 = st.columns([7, 2])
    with c1: st.markdown('<span style="font-size:24px;font-weight:700;color:#111827;">双镜</span>', unsafe_allow_html=True)
    with c2: st.markdown(f'<p style="text-align:right;color:#666;font-size:14px;">{st.session_state.username} | <span id="live-clock">{datetime.now().strftime("%H:%M:%S")}</span></p>', unsafe_allow_html=True)
    st.markdown("""
    <script>
        setInterval(function(){var d=new Date();var e=document.getElementById('live-clock');if(e)e.textContent=d.getHours().toString().padStart(2,'0')+':'+d.getMinutes().toString().padStart(2,'0')+':'+d.getSeconds().toString().padStart(2,'0');},1000);
    </script>
    """, unsafe_allow_html=True)

    # 4 KPI卡片
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.markdown(f'<div class="kpi-card"><div class="label">总资产</div><div class="value">{fmt_money(data["total_assets"] + bal)}</div></div>', unsafe_allow_html=True)
    with c2: st.markdown(f'<div class="kpi-card"><div class="label">可用余额</div><div class="value">{fmt_money(bal)}</div></div>', unsafe_allow_html=True)
    with c3: st.markdown(f'<div class="kpi-card"><div class="label">今日盈亏</div><div class="value">{fmt_money(data["total_pnl"])}</div><div class="delta {"up" if data["total_pnl"]>=0 else "down"}">{fmt_pct(data["pnl_ratio"])}</div></div>', unsafe_allow_html=True)
    with c4: st.markdown(f'<div class="kpi-card"><div class="label">收益率</div><div class="value" style="color:{"#16a34a" if data["pnl_ratio"]>=0 else "#ef4444"}">{fmt_pct(data["pnl_ratio"])}</div></div>', unsafe_allow_html=True)

    if data["stock_pnl"]:
        st.markdown('<div style="font-size:20px;font-weight:500;color:#111827;margin:24px 0 16px 0;">各股票盈亏</div>', unsafe_allow_html=True)
        df = pd.DataFrame(data["stock_pnl"])
        fig = go.Figure(go.Bar(
            x=df["name"], y=df["pnl"],
            marker_color="#16c757", text=[fmt_pnl(v) for v in df["pnl"]],
            textposition="outside", marker_line_width=0,
        ))
        fig.update_layout(
            margin=dict(t=16, b=0, l=20, r=20), height=380,
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=False, tickfont=dict(color="#666")),
            yaxis=dict(showgrid=False, tickfont=dict(color="#666"), zeroline=False),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

def page_portfolio():
    pf = get_user_portfolio(st.session_state.username)
    if pf.empty: st.info("暂无持仓"); return

    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{st.session_state.username}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:12px">我的持仓</div>""", unsafe_allow_html=True)

    # 移动端：卡片
    st.markdown('<div class="mobile-only">', unsafe_allow_html=True)
    for _, r in pf.iterrows():
        pct = r["pnl_ratio"]; cls = pnl_class(pct)
        st.markdown(f"""
        <div class="stock-card">
            <div class="sc-header">
                <span class="sc-name">{r["name"]} &nbsp;<span style="font-size:12px;color:#8A8AAA">{r["symbol"]}</span></span>
                <span class="sc-pct {cls}">{fmt_pct(pct)}</span>
            </div>
            <div class="sc-detail">
                <div>持仓 <span class="val">{fmt_num(r["shares"])}股</span></div>
                <div>成本 <span class="val">{fmt_money(r["avg_cost"])}</span></div>
                <div>现价 <span class="val">{fmt_money(r["current_price"])}</span></div>
                <div>盈亏 <span class="val" style="color:{pnl_color(r["pnl"])}">{fmt_money(r["pnl"])}</span></div>
            </div>
        </div>""", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # 桌面端：表格
    st.markdown('<div class="desktop-only"><div class="desktop-table">', unsafe_allow_html=True)
    d = pf[["name", "symbol", "shares", "avg_cost", "current_price", "market_value", "pnl", "pnl_ratio"]].copy()
    d.columns = ["名称", "代码", "持仓", "成本", "现价", "市值", "盈亏", "收益率"]
    d["成本"] = pf["avg_cost"].apply(lambda x: fmt_money(x))
    d["现价"] = pf["current_price"].apply(lambda x: fmt_money(x))
    d["市值"] = pf["market_value"].apply(lambda x: fmt_money(x))
    d["盈亏"] = pf["pnl"].apply(lambda x: fmt_money(x))
    d["收益率"] = pf["pnl_ratio"].apply(lambda x: f"{x:,.2f}%")
    st.dataframe(d[["名称", "代码", "持仓", "成本", "现价", "盈亏", "收益率"]], use_container_width=True, hide_index=True)
    st.markdown('</div></div>', unsafe_allow_html=True)

def page_market_making():
    mm = get_user_market_making(st.session_state.username)
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{st.session_state.username}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:12px">我的做市</div>""", unsafe_allow_html=True)
    if mm.empty: st.info("无做市记录"); return
    st.markdown('<div class="desktop-table">', unsafe_allow_html=True)
    d = mm.copy()
    d["卖出价"] = d["卖出价"].apply(lambda x: f"¥{x:,.2f}")
    d["当前价"] = d["当前价"].apply(lambda x: f"¥{x:,.2f}")
    d["对手方盈亏"] = d["对手方盈亏"].apply(lambda x: f"¥{x:,.2f}")
    st.dataframe(d, use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

def page_trade_hall():
    stocks = get_stocks()
    if not stocks: st.error("无股票"); return
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{st.session_state.username}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:12px">交易大厅</div>""", unsafe_allow_html=True)

    if not is_market_open():
        st.warning("市场已闭市，无法交易。等待管理员开市。")
        return

    # 桌面端表单
    st.markdown('<div class="desktop-only">', unsafe_allow_html=True)
    opts = {f"{s['name']} ({s['symbol']}) - {fmt_money(s['current_price'])}": s for s in stocks}
    with st.form("trade_form_desk"):
        sel = st.selectbox("股票", list(opts.keys()))
        s = opts[sel]
        c1, c2 = st.columns(2)
        with c1: direction = st.radio("方向", ["买入", "卖出"], horizontal=True)
        with c2:
            price = st.number_input("价格", min_value=0.01, value=float(s["current_price"]), step=0.5, format="%.2f")
            shares = st.number_input("数量(股)", min_value=1, step=100, format="%d")
        if st.form_submit_button("确认交易", type="primary", use_container_width=True):
            tt = "buy" if direction == "买入" else "sell"
            ok, msg = add_trade(st.session_state.username, s["symbol"], tt, price, shares)
            if ok: st.success(msg)
            else: st.error(msg)
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    # 因子面板（桌面端）
    st.markdown('<div style="margin-top:20px;">', unsafe_allow_html=True)
    st.markdown("""<div style="font-size:20px;font-weight:500;color:#111827;margin-bottom:12px">定价因子</div>""", unsafe_allow_html=True)
    factor_sym = st.selectbox("查看股票", [f"{s['name']}({s['symbol']})" for s in stocks], key="factor_sel")
    fsym = factor_sym.split("(")[1].rstrip(")")
    fs = next(x for x in stocks if x["symbol"] == fsym)
    prev = fs["previous_close"] or fs["current_price"]
    prem_f = round(1 + 0.2 * (fs["premium_rate"] - 50) / 50, 4)
    cm = max(fs["industry_carbon_mean"], 1)
    carb_f = round(1 - 0.5 * (fs["carbon_price"] - cm) / cm, 4)
    # 幸福度（溢价率）
    st.markdown(f"""
    <div style="background:#fff;border-radius:10px;padding:16px 20px;margin-bottom:12px;box-shadow:0 2px 10px rgba(0,0,0,.04);">
        <div style="font-size:13px;color:#666;margin-bottom:8px;">幸福度（溢价率）对价格的影响</div>
        <div style="display:flex;align-items:center;gap:12px;">
            <div style="flex:1;background:#e8ecf1;border-radius:6px;height:8px;overflow:hidden;">
                <div style="width:{fs['premium_rate']}%;height:100%;background:#{ '16a34a' if prem_f >= 1 else 'ef4444' };border-radius:6px;"></div>
            </div>
            <span style="font-size:28px;font-weight:600;color:#{ '16a34a' if prem_f >= 1 else 'ef4444' };">{prem_f}</span>
        </div>
        <div style="font-size:12px;color:#999;margin-top:4px;">溢价率 {fs['premium_rate']:.0f}% → 因子 {prem_f}</div>
    </div>""", unsafe_allow_html=True)
    # 碳排放（碳价）
    st.markdown(f"""
    <div style="background:#fff;border-radius:10px;padding:16px 20px;margin-bottom:12px;box-shadow:0 2px 10px rgba(0,0,0,.04);">
        <div style="font-size:13px;color:#666;margin-bottom:8px;">碳排放（碳价）对价格的影响</div>
        <div style="display:flex;align-items:center;gap:12px;">
            <div style="flex:1;background:#e8ecf1;border-radius:6px;height:8px;overflow:hidden;">
                <div style="width:{max(0, min(100, (1-carb_f)*200+50)):.0f}%;height:100%;background:#{ '16a34a' if carb_f >= 1 else 'ef4444' };border-radius:6px;"></div>
            </div>
            <span style="font-size:28px;font-weight:600;color:#{ '16a34a' if carb_f >= 1 else 'ef4444' };">{carb_f}</span>
        </div>
        <div style="font-size:12px;color:#999;margin-top:4px;">碳价 {fs['carbon_price']:.0f}（均值 {cm:.0f}）→ 因子 {carb_f} · 碳价越低价格越涨</div>
    </div>""", unsafe_allow_html=True)
    # 价格
    st.markdown(f"""
    <div style="background:#fff;border-radius:10px;padding:16px 20px;margin-bottom:12px;box-shadow:0 2px 10px rgba(0,0,0,.04);">
        <div style="display:flex;justify-content:space-around;text-align:center;">
            <div><div style="font-size:12px;color:#666;">昨收价</div><div style="font-size:20px;font-weight:600;color:#111827;">{fmt_money(prev)}</div></div>
            <div><div style="font-size:12px;color:#666;">理论价</div><div style="font-size:20px;font-weight:600;color:#111827;">{fmt_money(round(prev*max(1,prem_f)*carb_f,2))}</div></div>
            <div><div style="font-size:12px;color:#666;">涨跌停</div><div style="font-size:20px;font-weight:600;color:#111827;">{fmt_money(prev*0.9)}~{fmt_money(prev*1.1)}</div></div>
        </div>
    </div>""", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # 移动端底部交易栏 + 持仓列表
    st.markdown('<div class="mobile-only">', unsafe_allow_html=True)
    pf = get_user_portfolio(st.session_state.username)
    if not pf.empty:
        for _, r in pf.iterrows():
            pct = r["pnl_ratio"]; cls = pnl_class(pct)
            st.markdown(f"""
            <div class="stock-card">
                <div class="sc-header">
                    <span class="sc-name">{r["name"]} <span style="font-size:12px;color:#8A8AAA">{r["symbol"]}</span></span>
                    <span class="sc-pct {cls}">{fmt_pct(pct)}</span>
                </div>
                <div class="sc-detail">
                    <div>持仓 <span class="val">{fmt_num(r["shares"])}股</span></div>
                    <div>现价 <span class="val">{fmt_money(r["current_price"])}</span></div>
                    <div>成本 <span class="val">{fmt_money(r["avg_cost"])}</span></div>
                    <div>盈亏 <span class="val" style="color:{pnl_color(r["pnl"])}">{fmt_money(r["pnl"])}</span></div>
                </div>
            </div>""", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # 移动端固定底部交易栏
    st.markdown('<div class="mobile-only trade-bar-spacer"></div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="mobile-only trade-bar">
        <select id="tradeSymbol" style="flex:1;height:40px;border:1px solid #E8E8F0;border-radius:8px;padding:0 10px;font-size:14px;font-family:inherit;background:#F5F7FA">
            {''.join(f'<option value="{s["symbol"]}">{s["name"]}</option>' for s in stocks)}
        </select>
        <select id="tradeDir" style="flex:1;height:40px;border:1px solid #E8E8F0;border-radius:8px;padding:0 10px;font-size:14px;font-family:inherit;background:#F5F7FA">
            <option value="buy">买入</option>
            <option value="sell">卖出</option>
        </select>
        <input id="tradeQty" type="number" value="100" min="1" step="100" style="flex:1;height:40px;border:1px solid #E8E8F0;border-radius:8px;padding:0 10px;font-size:14px;font-family:inherit;background:#F5F7FA">
        <input id="tradePrice" type="number" value="{stocks[0]["current_price"]:.2f}" step="0.5" style="flex:1;height:40px;border:1px solid #E8E8F0;border-radius:8px;padding:0 10px;font-size:14px;font-family:inherit;background:#F5F7FA">
    </div>""", unsafe_allow_html=True)

def page_kline():
    stocks = get_stocks()
    if not stocks: st.info("无数据"); return
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{st.session_state.username}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:12px">K 线展板</div>""", unsafe_allow_html=True)
    opts = {f"{s['name']} ({s['symbol']})": s for s in stocks}
    sel = st.selectbox("选择股票", list(opts.keys()))
    sym = opts[sel]["symbol"]; s = opts[sel]
    data = get_kline_data(sym)

    # 轮次筛选
    if data:
        max_round = max(d["round"] for d in data)
        round_options = ["全部"] + [f"第{r}轮" for r in range(1, max_round + 1)]
        round_sel = st.selectbox("筛选轮次", round_options, key="kline_round")
        if round_sel != "全部":
            target_r = int(round_sel.replace("第","").replace("轮",""))
            data = [d for d in data if d["round"] == target_r]
            if len(data) == 1:
                data = [
                    {"round": 1, "open_price": data[0]["open_price"], "high_price": data[0]["high_price"],
                     "low_price": data[0]["low_price"], "close_price": data[0]["close_price"],
                     "volume": data[0]["volume"], "change_pct": data[0]["change_pct"]}
                ]

    if not data:
        import numpy as np; np.random.seed(42)
        base = s["current_price"]; closes = [base]
        for i in range(49): closes.append(max(base * .5, min(base * 1.5, closes[-1] + np.random.normal(0, base * .025))))
        data = []
        for i in range(1, len(closes)):
            o, c = closes[i - 1], closes[i]
            h = max(o, c) * (1 + abs(np.random.normal(0, .01))); l = min(o, c) * (1 - abs(np.random.normal(0, .01)))
            data.append({"round": i, "open_price": round(o, 2), "high_price": round(h, 2), "low_price": round(l, 2), "close_price": round(c, 2), "volume": abs(int(np.random.normal(5000, 2000))), "change_pct": round((c - o) / o * 100, 2)})
    df_k = pd.DataFrame(data)
    if df_k.empty: return

    colors = [GREEN if r["close_price"] >= r["open_price"] else RED for _, r in df_k.iterrows()]
    vol_c = ["rgba(34,197,94,0.5)" if r["close_price"] >= r["open_price"] else "rgba(239,68,68,0.5)" for _, r in df_k.iterrows()]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.03, row_heights=[.7, .3])

    fig.add_trace(go.Candlestick(
        x=df_k.index, open=df_k["open_price"], high=df_k["high_price"],
        low=df_k["low_price"], close=df_k["close_price"],
        increasing_line_color=GREEN, decreasing_line_color=RED,
        name="", showlegend=False,
    ), row=1, col=1)

    fig.add_trace(go.Bar(
        x=df_k.index, y=df_k["volume"], marker_color=vol_c,
        name="", showlegend=False,
    ), row=2, col=1)

    if len(df_k) >= 5:
        ma5 = df_k["close_price"].rolling(5).mean()
        fig.add_trace(go.Scatter(x=df_k.index, y=ma5, mode="lines",
            line=dict(color="#f59e0b", width=1.2), name="MA5"), row=1, col=1)
    if len(df_k) >= 10:
        ma10 = df_k["close_price"].rolling(10).mean()
        fig.add_trace(go.Scatter(x=df_k.index, y=ma10, mode="lines",
            line=dict(color="#3b82f6", width=1.2), name="MA10"), row=1, col=1)

    fig.update_layout(
        height=440, margin=dict(t=30, b=0, l=10, r=10),
        plot_bgcolor="#fafbfc", paper_bgcolor="#fafbfc",
        xaxis_rangeslider_visible=False,
        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02),
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=True, gridcolor="#e5e7eb", row=1, col=1)
    fig.update_xaxes(showgrid=True, gridcolor="#e5e7eb", title_text="轮次", row=2, col=1)
    fig.update_yaxes(showgrid=True, gridcolor="#e5e7eb", tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(showgrid=True, gridcolor="#e5e7eb", tickformat=",.0f", row=2, col=1)

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    if data:
        st.divider()
        st.markdown("""<div style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:8px">每轮数据明细</div>""", unsafe_allow_html=True)
        disp = pd.DataFrame(data).tail(30).copy()
        disp["开盘"] = disp["open_price"].apply(lambda x: f"¥{x:,.2f}")
        disp["最高"] = disp["high_price"].apply(lambda x: f"¥{x:,.2f}")
        disp["最低"] = disp["low_price"].apply(lambda x: f"¥{x:,.2f}")
        disp["收盘"] = disp["close_price"].apply(lambda x: f"¥{x:,.2f}")
        disp["涨跌幅"] = disp["change_pct"].apply(lambda x: f"{x:+.2f}%")
        disp["成交量"] = disp["volume"].apply(lambda x: f"{x:,.0f}")
        st.dataframe(disp[["round","开盘","最高","最低","收盘","涨跌幅","成交量"]].rename(columns={"round":"轮次"}), use_container_width=True, hide_index=True)

def page_admin_stock_summary():
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{st.session_state.username}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:12px">股票汇总</div>""", unsafe_allow_html=True)
    stats = get_platform_stats()
    st.markdown(f"""<div class="kpi-grid">{kpi_card("总市值", fmt_money(stats["total_mv"]))}{kpi_card("总盈亏", fmt_money(stats["total_pnl"]), fmt_pct(0) if stats["total_pnl"]==0 else None, stats["total_pnl"]>=0)}{kpi_card("活跃用户", fmt_num(stats["active_users"]))}<div></div></div>""", unsafe_allow_html=True)
    summary = get_admin_summary()
    if summary.empty: st.info("无数据"); return
    sdf = summary.sort_values("总盈亏")
    fig = go.Figure(go.Bar(x=sdf["股票名称"], y=sdf["总盈亏"], marker_color=[pnl_color(v) for v in sdf["总盈亏"]], text=[fmt_money(v) for v in sdf["总盈亏"]], textposition="outside"))
    fig.update_layout(height=280, margin=dict(t=8, b=0, l=0, r=0), plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)"); fig.update_xaxes(showgrid=False); fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    for _, row in summary.iterrows():
        with st.expander(f"{row['股票名称']} ({row['代码']})"):
            d = get_holder_detail(row["代码"])
            if not d.empty:
                dd = d.copy()
                dd["成本价"] = dd["成本价"].apply(lambda x: f"¥{x:,.2f}")
                dd["当前价"] = dd["当前价"].apply(lambda x: f"¥{x:,.2f}")
                dd["盈亏"] = dd["盈亏"].apply(lambda x: f"¥{x:,.2f}")
                dd["收益率"] = dd["收益率"].apply(lambda x: f"{x:,.2f}%")
                st.dataframe(dd, use_container_width=True, hide_index=True)
    disp = summary.copy()
    disp["当前价"] = disp["当前价"].apply(lambda x: f"¥{x:,.2f}")
    disp["总成本"] = disp["总成本"].apply(lambda x: f"¥{x:,.2f}")
    disp["总盈亏"] = disp["总盈亏"].apply(lambda x: f"¥{x:,.2f}")
    disp["收益率"] = disp["收益率"].apply(lambda x: f"{x:,.2f}%")
    st.markdown('<div class="desktop-table">', unsafe_allow_html=True)
    st.dataframe(disp, use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

def page_admin_stock_mgmt():
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{st.session_state.username}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:12px">股票管理</div>""", unsafe_allow_html=True)
    if st.session_state.get("stock_add_ok"): st.success(st.session_state.stock_add_ok); st.session_state.stock_add_ok = ""
    if st.session_state.get("stock_add_err"): st.error(st.session_state.stock_add_err); st.session_state.stock_add_err = ""
    with st.expander("添加新股票"):
        with st.form("add_stock_form"):
            c1, c2, c3 = st.columns(3)
            with c1: sym = st.text_input("代码", max_chars=10, key="asym")
            with c2: name = st.text_input("名称", key="aname")
            with c3: price = st.number_input("初始价", min_value=0.01, step=0.5, format="%.2f", key="aprice")
            if st.form_submit_button("添加", type="primary", use_container_width=True):
                s, n, p = sym.strip().upper(), name.strip(), price
                if s and n and p > 0:
                    ok, msg = add_stock(s, n, p)
                    if ok: st.session_state.stock_add_ok = msg
                    else: st.session_state.stock_add_err = msg
                else: st.session_state.stock_add_err = "请完整填写"
                st.rerun()
    stocks = get_stocks()
    if not stocks: st.info("无"); return
    sdf = pd.DataFrame(stocks)
    sdf["price"] = sdf["current_price"].apply(lambda x: f"¥{x:,.2f}")
    sdf["lu"] = sdf["last_update"].apply(lambda x: str(x)[:19] if x else "-")
    global_open = is_market_open()
    mkt_status = "交易中" if global_open else "已闭市"
    sdf["status"] = mkt_status
    st.markdown('<div class="desktop-table">', unsafe_allow_html=True)
    st.dataframe(sdf[["symbol", "name", "price", "status", "carbon_price", "premium_rate", "lu"]].rename(columns={"symbol": "代码", "name": "名称", "price": "当前价", "status": "状态", "carbon_price": "碳价", "premium_rate": "溢价率", "lu": "更新"}), use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)
    for s in stocks:
        with st.expander(f"{s['name']} ({s['symbol']})"):
            c1, c2, c3 = st.columns(3)
            with c1:
                np_ = st.number_input("新价格", min_value=0.01, step=0.5, format="%.2f", value=float(s["current_price"]), key=f"np_{s['id']}")
                if st.button("修改价格", key=f"up_{s['id']}"):
                    conn = get_db(); conn.execute("UPDATE stocks SET current_price=?,previous_close=? WHERE id=?", (np_, np_, s["id"])); conn.commit(); conn.close()
                    st.rerun()
            with c2:
                cp = st.number_input("碳价", value=float(s["carbon_price"]), step=1.0, format="%.1f", key=f"cp_{s['id']}")
                pr = st.number_input("溢价率", value=float(s["premium_rate"]), step=1.0, format="%.1f", key=f"pr_{s['id']}")
                if st.button("保存参数", key=f"sv_{s['id']}"): update_stock_params(s["id"], carbon_price=cp, premium_rate=pr); st.rerun()
            with c3:
                if st.button("删除", key=f"del_{s['id']}"): delete_stock(s["id"]); st.rerun()

def page_admin_user_mgmt():
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{st.session_state.username}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#1A1A2E;margin-bottom:12px">用户管理</div>""", unsafe_allow_html=True)
    users = get_all_users()
    df = pd.DataFrame(users)
    df["created_at"] = df["created_at"].apply(lambda x: str(x)[:19] if x else "-")
    df["状态"] = df.get("status", "active").fillna("active").map({"active": "正常", "disabled": "已禁用"})
    df.columns = ["ID", "用户名", "角色", "注册时间", "status_col", "状态"]
    df["角色"] = df["角色"].map({"admin": "管理员", "player": "选手"})
    st.markdown('<div class="desktop-table">', unsafe_allow_html=True)
    st.dataframe(df[["ID", "用户名", "角色", "状态", "注册时间"]], use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("""<div style="font-size:14px;font-weight:600;color:#1A1A2E;margin:20px 0 12px 0;">操作</div>""", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**重置密码**")
        with st.form("reset_pwd"):
            target = st.selectbox("用户", [u["username"] for u in users if u["role"] == "player"], key="rp_user")
            np_ = st.text_input("新密码", type="password", placeholder="至少4位")
            if st.form_submit_button("重置密码", type="primary", use_container_width=True):
                if target and np_ and len(np_) >= 4: reset_pwd(target, np_); st.success("已重置"); st.rerun()
                else: st.warning("请完整填写")
    with c2:
        st.markdown("**启用/禁用账户**")
        with st.form("toggle_user"):
            target2 = st.selectbox("用户", [u["username"] for u in users if u["role"] == "player"], key="tg_user")
            cur_status = next((u.get("status", "active") for u in users if u["username"] == target2), "active")
            btn_label = "禁用" if cur_status != "disabled" else "启用"
            if st.form_submit_button(btn_label, type="primary", use_container_width=True):
                toggle_user(target2); st.success(f"{target2} 已{btn_label}"); st.rerun()
def page_admin_settle():
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{st.session_state.username}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:20px;font-weight:500;color:#111827;margin-bottom:16px">市场控制</div>""", unsafe_allow_html=True)

    market_open = is_market_open()
    current_round = get_market_round()
    status = "交易中" if market_open else "已闭市"
    color = "#16a34a" if market_open else "#ef4444"

    # 初始化确认状态
    for k in ["cf_close", "cf_open", "cf_undo", "cf_r1"]:
        if k not in st.session_state: st.session_state[k] = False

    st.markdown(f"""
    <div style="background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 10px rgba(0,0,0,.04);text-align:center;margin-bottom:20px;">
        <div style="font-size:13px;color:#666;">当前市场状态</div>
        <div style="font-size:36px;font-weight:700;color:{color};margin:8px 0;">{status}</div>
        <div style="font-size:14px;color:#8b949e;">第 {current_round} 轮</div>
    </div>""", unsafe_allow_html=True)

    # 开关按钮 + 防误触确认
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if not st.session_state.cf_close:
            if st.button("一键闭市", type="primary", use_container_width=True, disabled=not market_open):
                st.session_state.cf_close = True; st.session_state.cf_open = False; st.session_state.cf_undo = False; st.session_state.cf_r1 = False; st.rerun()
        else:
            st.warning("确认闭市？所有选手将无法交易")
            cc1, cc2 = st.columns(2)
            with cc1:
                if st.button("确认闭市", type="primary", use_container_width=True):
                    close_market(); st.session_state.cf_close = False; st.session_state.cf_open = False; st.rerun()
            with cc2:
                if st.button("取消", use_container_width=True):
                    st.session_state.cf_close = False; st.rerun()

    with c2:
        if not st.session_state.cf_open:
            if st.button("一键开市", use_container_width=True, disabled=market_open):
                st.session_state.cf_open = True; st.session_state.cf_close = False; st.session_state.cf_undo = False; st.session_state.cf_r1 = False; st.rerun()
        else:
            st.info("确认开市？将进入新一轮交易")
            cc1, cc2 = st.columns(2)
            with cc1:
                if st.button("确认开市", type="primary", use_container_width=True):
                    open_market(); st.session_state.cf_open = False; st.rerun()
            with cc2:
                if st.button("取消", use_container_width=True):
                    st.session_state.cf_open = False; st.rerun()

    with c3:
        if not st.session_state.cf_undo:
            if st.button("撤销上一轮", use_container_width=True, disabled=current_round <= 1):
                st.session_state.cf_undo = True; st.session_state.cf_close = False; st.session_state.cf_open = False; st.session_state.cf_r1 = False; st.rerun()
        else:
            st.warning(f"确认回退到第 {current_round - 1} 轮？将删除最新K线")
            cc1, cc2 = st.columns(2)
            with cc1:
                if st.button("确认撤销", type="primary", use_container_width=True):
                    undo_market(); st.session_state.cf_undo = False; st.rerun()
            with cc2:
                if st.button("取消", use_container_width=True):
                    st.session_state.cf_undo = False; st.rerun()

    with c4:
        if not st.session_state.cf_r1:
            if st.button("回到第一轮", use_container_width=True, disabled=current_round <= 1):
                st.session_state.cf_r1 = True; st.session_state.cf_close = False; st.session_state.cf_open = False; st.session_state.cf_undo = False; st.rerun()
        else:
            st.warning("确认回到第1轮？将清空所有K线历史")
            cc1, cc2 = st.columns(2)
            with cc1:
                if st.button("确认重置", type="primary", use_container_width=True):
                    actual = reset_to_round1(); st.session_state.cf_r1 = False
                    st.success(f"已回到第 {actual} 轮"); st.rerun()
            with cc2:
                if st.button("取消", use_container_width=True):
                    st.session_state.cf_r1 = False; st.rerun()

    st.divider()

    # 每轮K线历史
    with st.expander("查看每轮K线历史"):
        stocks = get_stocks()
        sel_sym = st.selectbox("选择股票", [f"{s['name']} ({s['symbol']})" for s in stocks])
        sym = sel_sym.split("(")[1].rstrip(")")
        klines = get_kline_data(sym)
        if klines:
            df = pd.DataFrame(klines)
            df["开盘"] = df["open_price"].apply(lambda x: f"¥{x:,.2f}")
            df["最高"] = df["high_price"].apply(lambda x: f"¥{x:,.2f}")
            df["最低"] = df["low_price"].apply(lambda x: f"¥{x:,.2f}")
            df["收盘"] = df["close_price"].apply(lambda x: f"¥{x:,.2f}")
            df["涨跌幅"] = df["change_pct"].apply(lambda x: f"{x:+.2f}%")
            df["成交量"] = df["volume"].apply(lambda x: f"{x:,.0f}")
            st.dataframe(df[["round","开盘","最高","最低","收盘","涨跌幅","成交量"]].rename(columns={"round":"轮次"}), use_container_width=True, hide_index=True)
        else:
            st.info("暂无K线数据")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 导航 + main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NAV = {
    "总览": page_overview, "交易大厅": page_trade_hall,
    "我的持仓": page_portfolio, "我的做市": page_market_making,
    "K线展板": page_kline,     "市场控制": page_admin_settle,
    "股票汇总": page_admin_stock_summary,
    "股票管理": page_admin_stock_mgmt, "用户管理": page_admin_user_mgmt,
}
PLAYER_NAV = ["总览", "交易大厅", "我的持仓", "我的做市", "K线展板"]
ADMIN_NAV = ["市场控制", "股票汇总", "股票管理", "用户管理", "K线展板"]

st.set_page_config(page_title="双镜 - 智能投资分析系统", layout="wide", initial_sidebar_state="expanded")
st.markdown(RESPONSIVE_CSS + SIDEBAR_CSS, unsafe_allow_html=True)
init_db()

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False; st.session_state.username = ""; st.session_state.role = ""

def main():
    if not st.session_state.logged_in: page_login(); return
    with st.sidebar:
        role_text = "管理员" if st.session_state.role == "admin" else "选手"
        bal = get_user_balance(st.session_state.username)
        bal_text = f" | {fmt_money(bal)}" if st.session_state.role == "player" else ""
        st.markdown(f"""
        <div class="sb-brand"><div class="name">双镜</div><div class="sub">INSIGHT+</div></div>
        <div class="sb-user"><div class="uname">{st.session_state.username}</div><div class="urole"><span class="dot"></span>{role_text}{bal_text}</div></div>
        """, unsafe_allow_html=True)
        nav = ADMIN_NAV if st.session_state.role == "admin" else PLAYER_NAV
        st.markdown('<div class="menu-group-label">导航</div>', unsafe_allow_html=True)
        sel = st.radio("", nav, key="nav_main", label_visibility="collapsed")
        st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
        if st.button("退出登录", type="primary", use_container_width=True, key="sb_exit"):
            st.session_state.logged_in = False
            st.session_state.username = ""
            st.session_state.role = ""
            st.rerun()
    if sel in NAV: NAV[sel]()
    else: page_overview()

if __name__ == "__main__":
    main()
