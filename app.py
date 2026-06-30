"""
股票交易系统 — 移动端优先响应式版本
商业模拟挑战赛 · 零图标纯文字 · 触屏友好
"""
import os, hashlib, secrets
from contextlib import contextmanager
from datetime import datetime

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sqlite3
from kline_tradingview import page_kline_tradingview

@contextmanager
def get_db_cm():
    """带异常安全的数据库连接上下文管理器"""
    conn = sqlite3.connect("data/stock_analysis.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()

def row_get(row, key, default=None):
    try: return row[key]
    except (KeyError, IndexError, TypeError): return default

def hash_pwd(p, salt=""): return hashlib.sha256((p + salt).encode()).hexdigest()

def esc(s):
    """防 XSS：转义 HTML 特殊字符"""
    if not isinstance(s, str): return str(s)
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\"","&quot;").replace("'","&#x27;")

def check_pwd(stored, plain):
    """验证密码，兼容旧版无盐哈希"""
    if ":" in stored:
        salt, h = stored.split(":", 1)
        return hash_pwd(plain, salt) == h
    return hash_pwd(plain) == stored

def make_pwd(plain):
    """生成加盐密码"""
    salt = secrets.token_hex(8)
    return f"{salt}:{hash_pwd(plain, salt)}"

def get_admin_password():
    """从环境变量或 Streamlit secrets 读取管理员密码"""
    pw = os.environ.get("ADMIN_PASSWORD")
    if pw:
        return pw
    try:
        return st.secrets.get("ADMIN_PASSWORD", "")
    except Exception:
        return ""

def init_db():
    with get_db_cm() as conn:
        # 建表
        sql_tables = """
            CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT DEFAULT 'player', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT 'active', balance REAL DEFAULT 1000000);
            CREATE TABLE IF NOT EXISTS stocks(id INTEGER PRIMARY KEY, symbol TEXT UNIQUE NOT NULL, name TEXT NOT NULL, current_price REAL DEFAULT 0, previous_close REAL DEFAULT 0, is_deleted INTEGER DEFAULT 0, total_shares REAL DEFAULT 10000, revenue REAL DEFAULT 100000, industry_pe REAL DEFAULT 20, carbon_price REAL DEFAULT 50, industry_carbon_mean REAL DEFAULT 50, premium_rate REAL DEFAULT 50, init_funds REAL DEFAULT 5000, last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY, username TEXT NOT NULL, stock_symbol TEXT NOT NULL, trade_type TEXT NOT NULL, price REAL NOT NULL, shares INTEGER NOT NULL, round INTEGER DEFAULT 0, trade_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS kline(id INTEGER PRIMARY KEY, stock_symbol TEXT NOT NULL, round INTEGER DEFAULT 0, open_price REAL DEFAULT 0, high_price REAL DEFAULT 0, low_price REAL DEFAULT 0, close_price REAL DEFAULT 0, volume REAL DEFAULT 0, buy_total REAL DEFAULT 0, sell_total REAL DEFAULT 0, change_pct REAL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS rounds(stock_symbol TEXT NOT NULL, round INTEGER DEFAULT 0, is_settled INTEGER DEFAULT 0, PRIMARY KEY(stock_symbol, round));
            CREATE TABLE IF NOT EXISTS market_state(id INTEGER PRIMARY KEY, state TEXT DEFAULT 'open', round INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT DEFAULT '', detail TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS login_attempts(id INTEGER PRIMARY KEY, username TEXT NOT NULL, attempt_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS order_book(id INTEGER PRIMARY KEY, username TEXT NOT NULL, stock_symbol TEXT NOT NULL, trade_type TEXT NOT NULL, price REAL NOT NULL, shares INTEGER NOT NULL, round INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        """
        for stmt in sql_tables.split(';'):
            s = stmt.strip()
            if s:
                conn.execute(s + ';')
        conn.commit()
        # 迁移：revenue 字段
        try: conn.execute("ALTER TABLE stocks ADD COLUMN IF NOT EXISTS revenue DOUBLE PRECISION DEFAULT 100000")
        except: pass
        conn.execute("UPDATE stocks SET revenue=100000 WHERE revenue IS NULL OR revenue=0")
        conn.execute("INSERT INTO market_state(state,round) SELECT 'open',1 WHERE NOT EXISTS(SELECT 1 FROM market_state)")
        first_boot = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        if first_boot:
            _seed(conn)
        else:
            # 仅在显式配置 ADMIN_PASSWORD 时同步管理员密码，避免重启覆盖后台改密。
            admin_pw = get_admin_password()
            if admin_pw:
                conn.execute("UPDATE users SET password=? WHERE username='admin'", (make_pwd(admin_pw),))
            # 每次启动同步股票数据（覆盖更新）
            # 格式: (代码, 名称, 总股本, 净利润, 行业PE, 初始碳排, 碳排均值, 幸福度)
            stock_defs = [
                ("WULIU", "物流1公司", 10000, 200, 20, 50, 50, 50),
                ("JXIAO", "经销1公司", 10000, 300, 20, 50, 50, 50),
                ("JGONG", "加工1公司", 10000, 400, 20, 50, 50, 50),
                ("YLIAO", "原料1公司", 10000, 500, 20, 50, 50, 50),
            ]
            for sym, name, ts_, rev, ipe, cp, icm, pr in stock_defs:
                price = calc_initial_price(rev, ts_, ipe, sym)
                exists = conn.execute("SELECT id FROM stocks WHERE symbol=?", (sym,)).fetchone()
                if exists:
                    # 不覆盖 current_price/previous_close（保留交易产生的价格变化）
                    conn.execute("""UPDATE stocks SET name=?,is_deleted=0,
                        total_shares=?,revenue=?,industry_pe=?,carbon_price=?,industry_carbon_mean=?,premium_rate=? WHERE symbol=?""",
                        (name, ts_, rev, ipe, cp, icm, pr, sym))
                else:
                    conn.execute("""INSERT INTO stocks(symbol,name,current_price,previous_close,init_funds,
                        total_shares,revenue,industry_pe,carbon_price,industry_carbon_mean,premium_rate)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (sym, name, price, price, price * 10000 * 20 / 10000, ts_, rev, ipe, cp, icm, pr))
            # 仅当K线表为空时才重新生成（不覆盖交易产生的K线）
            has_kline = conn.execute("SELECT 1 FROM kline LIMIT 1").fetchone()
            if not has_kline:
                for s_def in stock_defs:
                    conn.execute("DELETE FROM kline WHERE stock_symbol=?", (s_def[0],))
            conn.commit()

        # 仅在首次部署时生成种子K线数据（reset_to_round1 删掉后不重新生成）
        if first_boot:
            import random as _rand
            _rand.seed(42)
            def _gen_kline(start_price, trend=1.0, vol_base=5000):
                rows = []
                p = float(start_price)
                for i in range(20):
                    o = p
                    drift = (trend - 1.0) * 0.3 / 20
                    noise = _rand.gauss(0, 0.025)
                    ret = drift + noise
                    c = round(p * (1 + ret), 2)
                    c = max(c, 0.01)
                    spread = abs(c - o) * 0.3 + 0.05
                    h = round(max(o, c) * (1 + _rand.uniform(0, spread)), 2)
                    l = round(min(o, c) * (1 - _rand.uniform(0, spread)), 2)
                    v = int(_rand.gauss(vol_base, vol_base * 0.3))
                    v = max(v, 100)
                    rows.append((o, h, l, c, v))
                    p = c
                return rows

            kline_seed = {
                "WULIU": _gen_kline(10.0, trend=1.08, vol_base=5000),
                "JXIAO": _gen_kline(15.0, trend=1.02, vol_base=7000),
                "JGONG": _gen_kline(20.0, trend=1.15, vol_base=10000),
                "YLIAO": _gen_kline(25.0, trend=0.92, vol_base=15000),
            }
            for s in conn.execute("SELECT * FROM stocks WHERE is_deleted=0").fetchall():
                sym = s["symbol"]
                klines = kline_seed.get(sym)
                if not klines:
                    continue
                conn.execute("DELETE FROM kline WHERE stock_symbol=?", (sym,))
                for r, (o, h, l, c, v) in enumerate(klines, 1):
                    cpct = round((c - o) / o * 100, 2) if o else 0
                    conn.execute("INSERT INTO rounds(stock_symbol,round,is_settled) VALUES(?,?,1) ON CONFLICT DO NOTHING", (sym, r))
                    conn.execute("INSERT INTO kline(stock_symbol,round,open_price,high_price,low_price,close_price,volume,buy_total,sell_total,change_pct) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (sym, r, o, h, l, c, v, v*0.6, v*0.4, cpct))
                last_c = klines[-1][3]
                conn.execute("UPDATE stocks SET current_price=?, previous_close=? WHERE symbol=?", (last_c, klines[-2][3] if len(klines) > 1 else klines[0][0], sym))
            conn.commit()
        # 首次启动时设置市场轮次 = 最大K线轮次 + 1，后续不覆盖
        if first_boot:
            max_round = conn.execute("SELECT COALESCE(MAX(round),0) FROM kline").fetchone()[0]
            next_round = max_round + 1
            conn.execute("UPDATE market_state SET round=?, state='open' WHERE id=1", (next_round,))
            for s in conn.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall():
                conn.execute("INSERT INTO rounds(stock_symbol,round,is_settled) VALUES(?,?,0) ON CONFLICT DO NOTHING", (s["symbol"], next_round))
            conn.commit()
        else:
            # 非首次启动不自动改 market_state.round，避免部署/唤醒导致赛程跳轮。
            state_row = conn.execute("SELECT state,round FROM market_state WHERE id=1").fetchone()
            if state_row and state_row["state"] == "open":
                active_symbols = [s["symbol"] for s in conn.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall()]
                for sym in active_symbols:
                    conn.execute("INSERT INTO rounds(stock_symbol,round,is_settled) VALUES(?,?,0) ON CONFLICT DO NOTHING", (sym, state_row["round"]))
                conn.commit()

def _seed(conn):
    admin_pw = get_admin_password() or "admin123"
    conn.execute("INSERT INTO users(id,username,password,role,created_at,status,balance) VALUES(?,?,?,'admin',CURRENT_TIMESTAMP,'active',1000000)", (1, "admin", make_pwd(admin_pw)))
    for i, u in enumerate(["player1", "player2", "player3"], 2):
        conn.execute("INSERT INTO users(id,username,password,role,created_at,status,balance) VALUES(?,?,?,'player',CURRENT_TIMESTAMP,'active',1000000)", (i, u, make_pwd(u)))
    for sym, name, ts_, rev, ipe, cp, icm, pr, funds in [
        ("WULIU", "物流1公司", 10000, 200, 20, 50, 50, 50, 2000),
        ("JXIAO", "经销1公司", 10000, 300, 20, 50, 50, 50, 3000),
        ("JGONG", "加工1公司", 10000, 400, 20, 50, 50, 50, 4000),
        ("YLIAO", "原料1公司", 10000, 500, 20, 50, 50, 50, 5000),
    ]:
        price = calc_initial_price(rev, ts_, ipe, sym)
        conn.execute("""INSERT INTO stocks(symbol,name,current_price,previous_close,init_funds,
            total_shares,revenue,industry_pe,carbon_price,industry_carbon_mean,premium_rate)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (sym, name, price, price, funds, ts_, rev, ipe, cp, icm, pr))
        conn.execute("INSERT INTO rounds(stock_symbol,round,is_settled) VALUES(?,1,0) ON CONFLICT DO NOTHING", (sym,))
    trades = [("player1", "WULIU", "buy", 9.5, 200, 1), ("player1", "JXIAO", "sell", 14.0, 100, 1), ("player1", "WULIU", "sell", 10.5, 80, 1), ("player2", "JGONG", "buy", 19.0, 150, 1), ("player2", "JXIAO", "sell", 16.0, 60, 1), ("player3", "WULIU", "buy", 10.0, 100, 1), ("player3", "YLIAO", "buy", 24.0, 80, 1), ("player2", "YLIAO", "buy", 26.0, 50, 1), ("player3", "JGONG", "sell", 21.0, 40, 1)]
    for args in trades: conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,?,?,?,?)", args)
    conn.commit()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 价格引擎（Excel公式）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def calc_pe(stock):
    """PE = 总市值 / 营业收入"""
    rev = row_get(stock, "revenue", 0)
    if not rev or rev <= 0: return row_get(stock, "industry_pe", 20)
    cp = row_get(stock, "current_price", 0) or row_get(stock, "previous_close", 0) or 0
    ts = row_get(stock, "total_shares", 10000)
    return round(cp * ts / rev, 2)

def calc_initial_price(revenue, total_shares, industry_pe, symbol=None):
    """初始价 = 净利润×10000÷总股本÷行业PE（Excel公式）。"""
    if not total_shares or not industry_pe or not revenue:
        return 0
    return round(revenue * 10000 / total_shares / industry_pe, 2)

def initial_price_warning(price):
    if price <= 0:
        return "初始价必须大于 0，请检查净利润、总股本和行业PE。"
    if price < 1 or price > 500:
        return f"公式价 ¥{price:,.2f} 明显异常，请检查是否把单位填错。常见合理值大约在 8.14、12.40、16.33、19.52 一类区间。"
    return ""

def compute_price(stock):
    prev = row_get(stock, "previous_close") or row_get(stock, "current_price") or 50
    bt = max(row_get(stock, "buy_total", 0), 1)
    st_ = max(row_get(stock, "sell_total", 0), 1)
    pf = 1 + 0.2 * (row_get(stock, "premium_rate", 50) - 50) / 50
    cm = max(row_get(stock, "industry_carbon_mean", 50), 1)
    cf = 1 - 0.5 * (row_get(stock, "carbon_price", 50) - cm) / cm
    t = prev * (bt / st_) * pf * cf
    return max(round(prev * 0.9, 2), min(round(prev * 1.1, 2), round(t, 2)))

def log_action(actor, action, target="", detail="", conn=None):
    if conn is not None:
        conn.execute("INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
            (actor or "system", action, str(target or ""), str(detail or "")))
    else:
        with get_db_cm() as c:
            c.execute("INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                (actor or "system", action, str(target or ""), str(detail or "")))
            c.commit()

def get_holding_shares(username, symbol, conn=None):
    if conn is not None:
        r = conn.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN trade_type='buy' THEN shares ELSE 0 END),0) AS bought,
                COALESCE(SUM(CASE WHEN trade_type IN('sell','force_close') THEN shares ELSE 0 END),0) AS sold
            FROM transactions
            WHERE username=? AND stock_symbol=?
        """, (username, symbol)).fetchone()
    else:
        with get_db_cm() as c:
            r = c.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN trade_type='buy' THEN shares ELSE 0 END),0) AS bought,
                    COALESCE(SUM(CASE WHEN trade_type IN('sell','force_close') THEN shares ELSE 0 END),0) AS sold
                FROM transactions
                WHERE username=? AND stock_symbol=?
            """, (username, symbol)).fetchone()
    return int((r["bought"] or 0) - (r["sold"] or 0)) if r else 0

def settle_round(symbol):
    with get_db_cm() as conn:
        stock = dict(conn.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone())
        r = conn.execute("SELECT MIN(round) FROM rounds WHERE stock_symbol=? AND is_settled=0", (symbol,)).fetchone()
        cr = r[0] if r and r[0] else 0
        if cr == 0: return None, False, 0, 0, 0, 0, 0
        txns = conn.execute("SELECT trade_type, price, shares FROM transactions WHERE stock_symbol=? AND round=?", (symbol, cr)).fetchall()
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
        pf = round(1 + 0.2 * (row_get(stock, "premium_rate", 50) - 50) / 50, 4)
        icm = max(row_get(stock, "industry_carbon_mean", 50), 1)
        cf = round(1 - 0.5 * (row_get(stock, "carbon_price", 50) - icm) / icm, 4)
        np_ = compute_price(dict(stock, buy_total=bt, sell_total=st_amt))
        raw = round((stock["previous_close"] or stock["current_price"]), 2) * (bt / max(st_amt, 1)) * pf * cf
        pc = stock["previous_close"] or stock["current_price"]
        cpct = round((np_ - pc) / pc * 100, 2) if pc else 0
        hi = max(np_, pc); lo = min(np_, pc)
        conn.execute("DELETE FROM kline WHERE stock_symbol=? AND round=?", (symbol, cr))
        conn.execute("INSERT INTO kline(stock_symbol,round,open_price,high_price,low_price,close_price,volume,buy_total,sell_total,change_pct) VALUES(?,?,?,?,?,?,?,?,?,?)", (symbol, cr, pc, hi, lo, np_, tv, bt, st_amt, cpct))
        nr = cr + 1
        conn.execute("UPDATE stocks SET previous_close=?,current_price=? WHERE symbol=?", (np_, np_, symbol))
        conn.execute("UPDATE rounds SET is_settled=1 WHERE stock_symbol=? AND round=?", (symbol, cr))
        conn.commit()
    get_stocks.clear()
    try: get_public_quote_snapshot.clear()
    except: pass
    return np_, matched, mp, mv_, pf, cf, round(raw, 2)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 用户 / 股票 / 持仓 / 汇总（保持原逻辑）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def auth_user(u, p):
    """SQLite 持久化限速：5 次失败 / 30 秒"""
    with get_db_cm() as conn:
        # 清理过期记录（超过30秒）
        conn.execute("DELETE FROM login_attempts WHERE username=? AND attempt_time < datetime('now', '-30 seconds')", (u,))
        # 检查最近失败次数
        recent = conn.execute("SELECT COUNT(*) FROM login_attempts WHERE username=? AND attempt_time > datetime('now', '-30 seconds')", (u,)).fetchone()
        if recent and recent[0] >= 5:
            return False, ""
        r = conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
    if not r or not check_pwd(r["password"], p):
        with get_db_cm() as conn:
            conn.execute("INSERT INTO login_attempts(username) VALUES(?)", (u,))
            conn.commit()
        return False, ""
    try:
        if r["status"] == "disabled": return False, ""
    except: pass
    return True, r["role"]

def toggle_user(username):
    with get_db_cm() as conn:
        cur = conn.execute("SELECT status FROM users WHERE username=? AND role='player'", (username,)).fetchone()
        if cur: new_s = "disabled" if cur["status"] != "disabled" else "active"
        else: return
        conn.execute("UPDATE users SET status=? WHERE username=?", (new_s, username))
        conn.commit()

def delete_user(username):
    with get_db_cm() as conn:
        conn.execute("DELETE FROM users WHERE username=? AND role='player'", (username,))
        conn.commit()

def register_user(u, p, role="player"):
    try:
        with get_db_cm() as conn:
            conn.execute("INSERT INTO users(username,password,role,balance) VALUES(?,?,?,1000000)", (u, make_pwd(p), role))
            conn.commit()
        return True, "注册成功"
    except Exception:
        return False, "用户名已存在"

def get_all_users():
    with get_db_cm() as conn:
        r = conn.execute("SELECT id,username,role,created_at,status FROM users ORDER BY id").fetchall()
    return [dict(x) for x in r]

def get_audit_logs(limit=80):
    with get_db_cm() as conn:
        r = conn.execute("SELECT actor,action,target,detail,created_at FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(x) for x in r]

def reset_pwd(u, np_):
    with get_db_cm() as conn:
        conn.execute("UPDATE users SET password=? WHERE username=?", (make_pwd(np_), u))
        conn.commit()

def get_stocks():
    """缓存10分钟（修改函数会显式清缓存），跨会话共享"""
    with get_db_cm() as conn:
        r = conn.execute("SELECT * FROM stocks WHERE is_deleted=0 ORDER BY symbol").fetchall()
    return [dict(x) for x in r]

def get_stock(sid):
    with get_db_cm() as conn:
        r = conn.execute("SELECT * FROM stocks WHERE id=?", (sid,)).fetchone()
    return dict(r) if r else None

def add_stock(sym, name, total_shares, revenue, industry_pe):
    try:
        with get_db_cm() as conn:
            price = calc_initial_price(revenue, total_shares, industry_pe, sym)
            warn = initial_price_warning(price)
            if warn:
                return False, warn
            funds = price * 10000 * 20 / 10000
            conn.execute("INSERT INTO stocks(symbol,name,current_price,previous_close,init_funds,total_shares,revenue,industry_pe) VALUES(?,?,?,?,?,?,?,?)",
                (sym.upper(), name, price, price, funds, total_shares, revenue, industry_pe))
            conn.execute("INSERT INTO rounds(stock_symbol,round,is_settled) VALUES(?,1,1) ON CONFLICT DO NOTHING", (sym.upper(),))
            conn.commit()
        get_stocks.clear()
        try: get_public_quote_snapshot.clear()
        except: pass
        return True, f"添加成功，初始价={price}"
    except Exception:
        return False, "代码已存在"

def update_stock_params(sid, **kw):
    """仅允许安全字段更新"""
    allowed = {"carbon_price", "premium_rate", "industry_carbon_mean", "revenue", "total_shares", "industry_pe"}
    safe = {k: v for k, v in kw.items() if k in allowed}
    if not safe: return
    with get_db_cm() as conn:
        sets = ", ".join(f"{k}=?" for k in safe)
        vals = list(safe.values()) + [sid]
        conn.execute(f"UPDATE stocks SET {sets} WHERE id=?", vals)
        conn.commit()
    get_stocks.clear()
    try: get_public_quote_snapshot.clear()
    except: pass

def delete_stock(sid):
    with get_db_cm() as conn:
        conn.execute("UPDATE stocks SET is_deleted=1 WHERE id=?", (sid,))
        conn.commit()
    get_stocks.clear()
    try: get_public_quote_snapshot.clear()
    except: pass


def _match_buy(conn, username, symbol, price, shares, cr, stock_name, bal):
    u"""买入撮合：扫卖一成交，剩余挂单或系统兜底"""
    remaining = shares
    matched = 0
    total = 0
    while remaining > 0:
        so = conn.execute("SELECT id,username,price,shares FROM order_book WHERE stock_symbol=? AND trade_type='sell' AND price<=? ORDER BY price ASC,id ASC LIMIT 1", (symbol, price)).fetchone()
        if not so: break
        ms = min(remaining, so["shares"]); mp = so["price"]; mc = ms * mp
        sh = get_holding_shares(so["username"], symbol, conn)
        sp = conn.execute("SELECT COALESCE(SUM(shares),0) FROM order_book WHERE username=? AND stock_symbol=? AND trade_type='sell' AND id!=?", (so["username"], symbol, so["id"])).fetchone()[0]
        if sh - sp < ms:
            conn.execute("DELETE FROM order_book WHERE id=?", (so["id"],)); continue
        conn.execute("UPDATE users SET balance=balance-? WHERE username=?", (mc, username))
        conn.execute("UPDATE users SET balance=balance+? WHERE username=?", (mc, so["username"]))
        conn.execute("UPDATE stocks SET current_price=? WHERE symbol=?", (mp, symbol))
        conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)", (username, symbol, mp, ms, cr))
        conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)", (so["username"], symbol, mp, ms, cr))
        matched += ms; total += mc; remaining -= ms
        ns = so["shares"] - ms
        if ns <= 0: conn.execute("DELETE FROM order_book WHERE id=?", (so["id"],))
        else: conn.execute("UPDATE order_book SET shares=? WHERE id=?", (ns, so["id"]))
    if matched and remaining:
        conn.execute("INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)", (username, symbol, price, remaining, cr))
        avg = total / matched
        return f"[成交] {matched}股 {stock_name}，均价{avg:.2f}，共{total:,.0f} | [挂单] {remaining}股 @ {price}", matched
    if matched:
        avg = total / matched
        return f"[全部成交] {stock_name} {matched}股 @ {avg:.2f}，花费{total:,.0f}", matched
    cost = price * remaining
    if bal and bal["balance"] >= cost:
        conn.execute("UPDATE users SET balance=balance-? WHERE username=?", (cost, username))
        conn.execute("UPDATE stocks SET current_price=?,previous_close=? WHERE symbol=?", (price, price, symbol))
        conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)", (username, symbol, price, remaining, cr))
        conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)", ("[系统]", symbol, price, remaining, cr))
        return f"[成交] {stock_name} {remaining}股 @ {price}，花费{cost:,.0f}", 0
    conn.execute("INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)", (username, symbol, price, remaining, cr))
    return f"[挂单] {stock_name} {price} x {remaining}股，等待成交", 0

def _match_sell(conn, username, symbol, price, shares, cr, stock_name):
    u"""卖出撮合：扫买一成交，剩余挂单或系统兜底"""
    remaining = shares
    matched = 0
    total = 0
    while remaining > 0:
        bo = conn.execute("SELECT id,username,price,shares FROM order_book WHERE stock_symbol=? AND trade_type='buy' AND price>=? ORDER BY price DESC,id ASC LIMIT 1", (symbol, price)).fetchone()
        if not bo: break
        ms = min(remaining, bo["shares"]); mp = bo["price"]; mc = ms * mp
        bb = conn.execute("SELECT balance FROM users WHERE username=?", (bo["username"],)).fetchone()
        if not bb or bb["balance"] < mc:
            conn.execute("DELETE FROM order_book WHERE id=?", (bo["id"],)); continue
        conn.execute("UPDATE users SET balance=balance-? WHERE username=?", (mc, bo["username"]))
        conn.execute("UPDATE users SET balance=balance+? WHERE username=?", (mc, username))
        conn.execute("UPDATE stocks SET current_price=? WHERE symbol=?", (mp, symbol))
        conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)", (bo["username"], symbol, mp, ms, cr))
        conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)", (username, symbol, mp, ms, cr))
        matched += ms; total += mc; remaining -= ms
        ns = bo["shares"] - ms
        if ns <= 0: conn.execute("DELETE FROM order_book WHERE id=?", (bo["id"],))
        else: conn.execute("UPDATE order_book SET shares=? WHERE id=?", (ns, bo["id"]))
    if matched and remaining:
        conn.execute("INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)", (username, symbol, price, remaining, cr))
        avg = total / matched
        return f"[成交] {matched}股 {stock_name}，均价{avg:.2f}，共{total:,.0f} | [挂单] {remaining}股 @ {price}", matched
    if matched:
        avg = total / matched
        return f"[全部成交] {stock_name} {matched}股 @ {avg:.2f}，收入{total:,.0f}", matched
    cost = price * remaining
    conn.execute("UPDATE users SET balance=balance+? WHERE username=?", (cost, username))
    conn.execute("UPDATE stocks SET current_price=?,previous_close=? WHERE symbol=?", (price, price, symbol))
    conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)", (username, symbol, price, remaining, cr))
    conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)", ("[系统]", symbol, price, remaining, cr))
    return f"[成交] {stock_name} {remaining}股 @ {price}，收入{cost:,.0f}", 0

def add_trade(username, symbol, tt, price, shares):
    u"""订单簿撮合：可立即成交则成交，否则挂单"""
    with get_db_cm() as conn:
        cr = conn.execute("SELECT MIN(round) FROM rounds WHERE stock_symbol=? AND is_settled=0", (symbol,)).fetchone()
        r = cr[0] if cr and cr[0] else 0
        if r == 0: return False, "市场已闭市，无法交易"
        sr = conn.execute("SELECT name FROM stocks WHERE symbol=? AND is_deleted=0", (symbol,)).fetchone()
        if not sr: return False, "股票不存在或已停用"
        nm = sr["name"]
        if tt == "buy":
            bal = conn.execute("SELECT balance FROM users WHERE username=?", (username,)).fetchone()
            if not bal: return False, "用户不存在"
            if bal["balance"] < price * shares:
                max_b = int(bal["balance"] / price)
                if max_b <= 0:
                    return False, f"余额不足，最多可买 {int(bal['balance']/price)} 股"
                msg, m = _match_buy(conn, username, symbol, price, max_b, r, nm, bal)
            else:
                msg, m = _match_buy(conn, username, symbol, price, shares, r, nm, bal)
        else:
            holding = get_holding_shares(username, symbol, conn)
            ps = conn.execute("SELECT COALESCE(SUM(shares),0) FROM order_book WHERE username=? AND stock_symbol=? AND trade_type='sell'", (username, symbol)).fetchone()[0]
            if holding - ps < shares:
                return False, f"可卖不足：持有{holding}股，已挂卖{ps}股，可用{holding-ps}股"
            msg, m = _match_sell(conn, username, symbol, price, shares, r, nm)
        log_action(username, f"trade_{tt}", symbol, f"round={r}, price={price}, shares={shares}, matched={m}", conn)
        conn.commit()
    get_stocks.clear()
    try: get_public_quote_snapshot.clear()
    except: pass
    return True, msg


def get_user_balance(username):
    with get_db_cm() as conn:
        r = conn.execute("SELECT balance FROM users WHERE username=?", (username,)).fetchone()
    return r["balance"] if r else 0

def is_market_open():
    with get_db_cm() as conn:
        r = conn.execute("SELECT state FROM market_state WHERE id=1").fetchone()
    return r["state"] == "open" if r else True

def get_market_round():
    with get_db_cm() as conn:
        r = conn.execute("SELECT round FROM market_state WHERE id=1").fetchone()
    return r["round"] if r else 1

def close_market():
    with get_db_cm() as conn:
        r = conn.execute("SELECT state FROM market_state WHERE id=1").fetchone()
        if r and r["state"] == "closed": return

        # 第一步：批量撮合所有挂单（成交数据计入当前轮次）
        mkt_round = conn.execute("SELECT round FROM market_state WHERE id=1").fetchone()
        mr = mkt_round["round"] if mkt_round else 1
        for stock in conn.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall():
            sym = stock["symbol"]
            buys = conn.execute("SELECT id,username,price,shares FROM order_book WHERE stock_symbol=? AND trade_type='buy' ORDER BY price DESC, id ASC", (sym,)).fetchall()
            sells = conn.execute("SELECT id,username,price,shares FROM order_book WHERE stock_symbol=? AND trade_type='sell' ORDER BY price ASC, id ASC", (sym,)).fetchall()
            if not buys or not sells:
                continue
            hb = buys[0]["price"]
            ls_ = sells[0]["price"]
            if hb < ls_:
                continue
            mp = round((hb + ls_) / 2, 2)
            tb = sum(b["shares"] for b in buys)
            ts = sum(s["shares"] for s in sells)
            mv_ = min(tb, ts)
            br = mv_ / tb if tb else 0
            sr = mv_ / ts if ts else 0
            for b in buys:
                fill = int(b["shares"] * br)
                if fill > 0:
                    c = round(fill * mp, 2)
                    conn.execute("UPDATE users SET balance=balance-? WHERE username=?", (c, b["username"]))
                    conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)",
                        (b["username"], sym, mp, fill, mr))
            for s in sells:
                fill = int(s["shares"] * sr)
                if fill > 0:
                    c = round(fill * mp, 2)
                    conn.execute("UPDATE users SET balance=balance+? WHERE username=?", (c, s["username"]))
                    conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,?)",
                        (s["username"], sym, mp, fill, mr))
        conn.execute("DELETE FROM order_book")

        # 第二步：结算K线（包含第一步撮合的交易）
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
                conn.execute("DELETE FROM kline WHERE stock_symbol=? AND round=?", (s["symbol"], cr))
                conn.execute("INSERT INTO kline(stock_symbol,round,open_price,high_price,low_price,close_price,volume,buy_total,sell_total,change_pct) VALUES(?,?,?,?,?,?,?,?,?,?)", (s["symbol"], cr, pc, hi, lo, np_, tv, bt, st_amt, cpct))
                conn.execute("UPDATE stocks SET previous_close=?,current_price=? WHERE symbol=?", (np_, np_, s["symbol"]))
                conn.execute("UPDATE rounds SET is_settled=1 WHERE stock_symbol=? AND round=?", (s["symbol"], cr))
        conn.execute("UPDATE market_state SET state='closed' WHERE id=1")
        conn.commit()

    try:
        get_public_quote_snapshot.clear()
    except Exception:
        pass
    get_stocks.clear()
    is_market_open.clear()
    get_market_round.clear()

def open_market():
    with get_db_cm() as conn:
        r = conn.execute("SELECT state,round FROM market_state WHERE id=1").fetchone()
        if not r or r["state"] == "open": return
        new_round = r["round"] + 1
        stocks = conn.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall()
        for s in stocks:
            conn.execute("INSERT INTO rounds(stock_symbol,round,is_settled) VALUES(?,?,0) ON CONFLICT DO NOTHING", (s["symbol"], new_round))
        conn.execute("UPDATE market_state SET state='open', round=? WHERE id=1", (new_round,))
        conn.commit()

    try:
        get_public_quote_snapshot.clear()
    except Exception:
        pass
    get_stocks.clear()
    is_market_open.clear()
    get_market_round.clear()

def undo_market():
    """撤销上一轮：回退到闭市前状态"""
    with get_db_cm() as conn:
        r = conn.execute("SELECT state,round FROM market_state WHERE id=1").fetchone()
        if not r or r["round"] <= 1: return
        # 检查市场是否为 closed 状态才允许撤销
        if r["state"] != "closed": return
        prev_round = r["round"] - 1
        stocks = conn.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall()
        for s in stocks:
            conn.execute("DELETE FROM kline WHERE stock_symbol=? AND round=?", (s["symbol"], r["round"]))
            conn.execute("DELETE FROM rounds WHERE stock_symbol=? AND round=?", (s["symbol"], r["round"]))
            prev_k = conn.execute("SELECT close_price FROM kline WHERE stock_symbol=? AND round=?", (s["symbol"], prev_round)).fetchone()
            if prev_k:
                conn.execute("UPDATE stocks SET previous_close=?, current_price=? WHERE symbol=?", (prev_k["close_price"], prev_k["close_price"], s["symbol"]))
        conn.execute("UPDATE market_state SET state='open', round=? WHERE id=1", (prev_round,))
        conn.commit()

    try:
        get_public_quote_snapshot.clear()
    except Exception:
        pass
    get_stocks.clear()
    is_market_open.clear()
    get_market_round.clear()

def reset_to_round1():
    """重开赛局：清空交易/K线/轮次，价格和资金回到初始状态，从第1轮重新开始。"""
    with get_db_cm() as conn:
        stocks = conn.execute("SELECT * FROM stocks WHERE is_deleted=0").fetchall()
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM order_book")
        conn.execute("DELETE FROM kline")
        conn.execute("DELETE FROM rounds")
        conn.execute("UPDATE users SET balance=1000000 WHERE role='player'")
        for s in stocks:
            init_price = calc_initial_price(
                row_get(s, "revenue", 0),
                row_get(s, "total_shares", 0),
                row_get(s, "industry_pe", 0),
                row_get(s, "symbol", ""),
            ) or row_get(s, "current_price", 0) or 1
            conn.execute(
                "UPDATE stocks SET current_price=?, previous_close=? WHERE symbol=?",
                (init_price, init_price, s["symbol"]),
            )
            conn.execute("INSERT INTO rounds(stock_symbol,round,is_settled) VALUES(?,1,0)", (s["symbol"],))
        conn.execute("UPDATE market_state SET state='open', round=1 WHERE id=1")
        conn.commit()
        # 验证删除
        remaining = conn.execute("SELECT COUNT(*) FROM kline").fetchone()[0]
        conn.execute("UPDATE market_state SET round=1 WHERE id=1")
        conn.commit()
    try:
        get_public_quote_snapshot.clear()
    except Exception:
        pass
    return 1

def get_user_portfolio(username):
    with get_db_cm() as conn:
        buys = conn.execute("SELECT stock_symbol,SUM(shares) s,SUM(price*shares) c FROM transactions WHERE username=? AND trade_type='buy' GROUP BY stock_symbol", (username,)).fetchall()
        sells = conn.execute("SELECT stock_symbol,SUM(shares) s FROM transactions WHERE username=? AND trade_type IN('sell','force_close') GROUP BY stock_symbol", (username,)).fetchall()
    sm = {r["stock_symbol"]: r["s"] for r in sells}
    stocks = {s["symbol"]: s for s in get_stocks()}
    rows = []
    for b in buys:
        sym = b["stock_symbol"]; net = b["s"] - sm.get(sym, 0)
        if net <= 0: continue
        avg = round(b["c"] / b["s"], 2) if b["s"] else 0
        info = stocks.get(sym, {"name": sym, "current_price": avg})
        cp = row_get(info, "current_price", avg); mv_ = round(cp * net, 2); pnl = round((cp - avg) * net, 2)
        rows.append({"symbol": sym, "name": info["name"], "shares": int(net), "avg_cost": avg, "current_price": cp, "market_value": mv_, "pnl": pnl, "pnl_ratio": round((cp - avg) / avg * 100, 2) if avg else 0})
    return pd.DataFrame(rows)

def get_user_market_making(username):
    with get_db_cm() as conn:
        rows = conn.execute("SELECT t.stock_symbol,t.price sp,t.shares,t.trade_date,COALESCE(s.current_price,t.price) cp,COALESCE(s.name,t.stock_symbol) nm FROM transactions t LEFT JOIN stocks s ON t.stock_symbol=s.symbol WHERE t.username=? AND t.trade_type='sell' ORDER BY t.trade_date DESC", (username,)).fetchall()
    return pd.DataFrame([{"股票": r["nm"], "卖出价": round(r["sp"], 2), "当前价": round(r["cp"], 2), "数量": r["shares"], "对手方盈亏": round((r["cp"] - r["sp"]) * r["shares"], 2), "时间": r["trade_date"]} for r in rows])

def get_user_overview(username):
    pf = get_user_portfolio(username)
    if pf.empty: return {"total_assets": 0, "total_cost": 0, "total_pnl": 0, "pnl_ratio": 0, "stock_count": 0, "stock_pnl": []}
    ta, tc = pf["market_value"].sum(), (pf["avg_cost"] * pf["shares"]).sum()
    tp = ta - tc
    return {"total_assets": round(ta, 2), "total_cost": round(tc, 2), "total_pnl": round(tp, 2), "pnl_ratio": round(tp / tc * 100, 2) if tc else 0, "stock_count": len(pf), "stock_pnl": pf[["name", "symbol", "pnl"]].to_dict("records")}

def get_admin_summary():
    """单条SQL汇总所有选手持仓数据，替代原来O(n)循环"""
    stocks = get_stocks()
    if not stocks: return pd.DataFrame()
    with get_db_cm() as conn:
        rows = conn.execute("""
            SELECT
                t.stock_symbol AS sym,
                COUNT(DISTINCT t.username) AS holder_cnt,
                SUM(CASE WHEN t.trade_type='buy' THEN t.shares ELSE 0 END) -
                SUM(CASE WHEN t.trade_type IN('sell','force_close') THEN t.shares ELSE 0 END) AS net_shares,
                SUM(CASE WHEN t.trade_type='buy' THEN t.shares ELSE 0 END) AS total_bought,
                SUM(CASE WHEN t.trade_type='buy' THEN t.price*t.shares ELSE 0 END) AS buy_cost
            FROM transactions t
            JOIN users u ON t.username = u.username
            WHERE u.role = 'player'
            GROUP BY t.stock_symbol
            HAVING net_shares > 0
        """).fetchall()
    holder_map = {r["sym"]: {"cnt": r["holder_cnt"], "shares": int(r["net_shares"]), "bought": int(r["total_bought"]), "cost": round(r["buy_cost"], 2)} for r in rows}
    result = []
    for s in stocks:
        sym = s["symbol"]
        h = holder_map.get(sym)
        if h:
            avg_cost = round(h["cost"] / h["bought"], 2) if h["bought"] else 0
            mv_ = round(s["current_price"] * h["shares"], 2)
            pnl = round((s["current_price"] - avg_cost) * h["shares"], 2)
            result.append({"股票名称": s["name"], "代码": sym, "当前价": s["current_price"],
                "持有用户数": h["cnt"], "总持仓量": h["shares"], "总成本": round(avg_cost * h["shares"], 2),
                "总市值": mv_, "总盈亏": pnl,
                "收益率": round(pnl / (avg_cost * h["shares"]) * 100, 2) if avg_cost and h["shares"] else 0})
        else:
            result.append({"股票名称": s["name"], "代码": sym, "当前价": s["current_price"],
                "持有用户数": 0, "总持仓量": 0, "总成本": 0, "总市值": 0, "总盈亏": 0, "收益率": 0})
    return pd.DataFrame(result)

def get_holder_detail(symbol):
    """单条SQL查询某只股票的持仓明细"""
    with get_db_cm() as conn:
        rows = conn.execute("""
            SELECT
                t.username,
                SUM(CASE WHEN t.trade_type='buy' THEN t.shares ELSE 0 END) AS bought,
                SUM(CASE WHEN t.trade_type IN('sell','force_close') THEN t.shares ELSE 0 END) AS sold,
                SUM(CASE WHEN t.trade_type='buy' THEN t.price*t.shares ELSE 0 END) AS buy_cost
            FROM transactions t
            WHERE t.stock_symbol=?
            GROUP BY t.username
        """, (symbol,)).fetchall()
    stock_info = get_stocks()
    sp = {s["symbol"]: s["current_price"] for s in stock_info}
    cp = sp.get(symbol, 0)
    r = []
    for row in rows:
        net = row["bought"] - row["sold"]
        if net <= 0: continue
        avg = round(row["buy_cost"] / row["bought"], 2) if row["bought"] else 0
        pnl = round((cp - avg) * net, 2)
        r.append({"用户名": row["username"], "持仓量": int(net),
                  "成本价": avg, "当前价": cp, "盈亏": pnl,
                  "收益率": round((cp - avg) / avg * 100, 2) if avg else 0})
    return pd.DataFrame(r)

def get_kline_data(symbol, include_live=False):
    """K线数据；默认只返回已结算K线，避免图表随当前轮临时成交跳动。"""
    with get_db_cm() as conn:
        r = conn.execute("""
            SELECT k.*
            FROM kline k
            JOIN (
                SELECT round, MAX(id) AS id
                FROM kline
                WHERE stock_symbol=?
                GROUP BY round
            ) latest ON latest.id = k.id
            ORDER BY k.round
        """, (symbol,)).fetchall()
        result = [dict(x) for x in r]

        if include_live:
            # 查找当前未结算轮次（选手正在交易的轮次）
            open_r = conn.execute("SELECT MIN(round) FROM rounds WHERE stock_symbol=? AND is_settled=0", (symbol,)).fetchone()
            cr = open_r[0] if open_r and open_r[0] else 0
        else:
            cr = 0
        if cr:
            stock = conn.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone()
            txns = conn.execute("SELECT trade_type,price,shares FROM transactions WHERE stock_symbol=? AND round=?", (symbol, cr)).fetchall()
            if txns and stock:
                prev = stock["previous_close"] or stock["current_price"]
                bt = sum(t["price"]*t["shares"] for t in txns if t["trade_type"]=="buy")
                st_amt = sum(t["price"]*t["shares"] for t in txns if t["trade_type"]=="sell")
                tv = sum(t["shares"] for t in txns)
                np_ = compute_price(dict(stock, buy_total=bt, sell_total=st_amt))
                hi = max(np_, prev); lo = min(np_, prev)
                cpct = round((np_-prev)/prev*100,2) if prev else 0
                # 移除可能存在的旧数据（闭市产生的同轮次K线），用实时数据替换
                result = [x for x in result if x["round"] != cr]
                result.append({
                    "stock_symbol": symbol, "round": cr,
                    "open_price": prev, "high_price": hi, "low_price": lo,
                    "close_price": np_, "volume": tv,
                    "buy_total": bt, "sell_total": st_amt, "change_pct": cpct,
                })
    return sorted(result, key=lambda x: x["round"])

def get_market_card_data(stock):
    """Return dashboard quote data from latest K-line instead of stale stock columns."""
    symbol = stock["symbol"]
    klines = get_kline_data(symbol, include_live=True)
    if klines:
        latest = klines[-1]
        ref_price = row_get(latest, "open_price", None) or row_get(stock, "previous_close", None) or row_get(stock, "current_price", 0)
        price = row_get(latest, "close_price", ref_price) or ref_price
        change = price - ref_price
        pct = change / ref_price * 100 if ref_price else 0
        total_vol = int(sum(row_get(d, "volume", 0) or 0 for d in klines[-5:]))
        buy_amt = sum(row_get(d, "buy_total", 0) or 0 for d in klines[-5:])
        sell_amt = sum(row_get(d, "sell_total", 0) or 0 for d in klines[-5:])
        latest_round = row_get(latest, "round", "-")
    else:
        price = row_get(stock, "current_price", 0) or 0
        ref_price = row_get(stock, "previous_close", price) or price
        change = price - ref_price
        pct = change / ref_price * 100 if ref_price else 0
        total_vol = 0
        buy_amt = sell_amt = 0
        latest_round = "-"
    return {
        "price": float(price or 0),
        "ref_price": float(ref_price or 0),
        "change": float(change or 0),
        "pct": float(pct or 0),
        "volume5": total_vol,
        "buy5": float(buy_amt or 0),
        "sell5": float(sell_amt or 0),
        "round": latest_round,
    }

@st.cache_data(ttl=1, show_spinner=False)
def get_public_quote_snapshot():
    stocks = get_stocks()
    quotes = {s["symbol"]: get_market_card_data(s) for s in stocks}
    return stocks, quotes

def get_platform_stats():
    s = get_admin_summary()
    if s.empty: return {"total_mv": 0, "total_pnl": 0, "active_users": 0}
    with get_db_cm() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM users WHERE role='player'").fetchone()[0]
    return {"total_mv": round((s["当前价"] * s["总持仓量"]).sum(), 2), "total_pnl": round(s["总盈亏"].sum(), 2), "active_users": cnt}

def get_competition_snapshot():
    mkt_open = is_market_open()
    mkt_round = get_market_round()
    return {
        "state": "交易中" if mkt_open else "已闭市",
        "ok": mkt_open,
        "round": int(mkt_round or 1),
    }

def get_admin_risk_overview():
    current_round = get_market_round()
    with get_db_cm() as conn:
        players = conn.execute("SELECT username,balance,status FROM users WHERE role='player' ORDER BY username").fetchall()
        trade_rows = conn.execute("""
            SELECT username, COUNT(*) AS cnt, MAX(trade_date) AS last_time
            FROM transactions
            WHERE round=? AND username NOT LIKE '[系统]'
            GROUP BY username
        """, (current_round,)).fetchall()
    trade_map = {r["username"]: {"cnt": int(r["cnt"] or 0), "last": r["last_time"]} for r in trade_rows}
    rows, warnings = [], []
    for p in players:
        username = p["username"]
        balance = float(row_get(p, "balance", 0) or 0)
        pf = get_user_portfolio(username)
        market_value = float(pf["market_value"].sum()) if not pf.empty else 0
        cost = float((pf["avg_cost"] * pf["shares"]).sum()) if not pf.empty else 0
        pnl = market_value - cost
        pnl_ratio = round(pnl / cost * 100, 2) if cost else 0
        total_assets = balance + market_value
        max_mv = float(pf["market_value"].max()) if not pf.empty else 0
        concentration = round(max_mv / market_value * 100, 2) if market_value else 0
        trade_cnt = trade_map.get(username, {}).get("cnt", 0)
        rows.append({
            "选手": username, "余额": balance, "持仓市值": market_value, "总资产": total_assets,
            "浮动盈亏": pnl, "收益率": pnl_ratio, "集中度": concentration,
            "持仓股票": len(pf), "本轮交易": trade_cnt, "状态": row_get(p, "status", "active"),
        })
        if balance <= 1000:
            warnings.append({"级别": "高", "对象": username, "信号": "现金接近耗尽", "数值": fmt_money(balance)})
        if pnl_ratio >= 80:
            warnings.append({"级别": "中", "对象": username, "信号": "收益率异常偏高", "数值": f"{pnl_ratio:,.2f}%"})
        if pnl_ratio < 0:
            level = "高" if pnl_ratio <= -30 else "中"
            warnings.append({"级别": level, "对象": username, "信号": "当前浮动亏损", "数值": f"{pnl_ratio:,.2f}%"})
        if pnl_ratio <= -30:
            warnings.append({"级别": "高", "对象": username, "信号": "收益率大幅回撤", "数值": f"{pnl_ratio:,.2f}%"})
        if concentration >= 80 and market_value > 0:
            warnings.append({"级别": "中", "对象": username, "信号": "持仓过度集中", "数值": f"{concentration:,.2f}%"})
        if trade_cnt >= 20:
            warnings.append({"级别": "中", "对象": username, "信号": "本轮交易过于活跃", "数值": fmt_num(trade_cnt)})
    df = pd.DataFrame(rows)
    warn_df = pd.DataFrame(warnings)
    if warn_df.empty:
        warn_df = pd.DataFrame([{"级别": "正常", "对象": "全场", "信号": "暂无明显异常", "数值": "-"}])
    return df, warn_df

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 响应式 CSS — 移动端优先
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESPONSIVE_CSS = """
<style>
* { box-sizing: border-box; }
html, body, .stApp {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", sans-serif;
    -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}
#MainMenu, .stDeployButton, footer, [data-testid="stStatusWidget"],
[data-testid="stDecoration"], [data-testid="stToolbar"],
[data-testid="manage-app-button"] { display: none !important; }

/* 全局 — 深色交易终端 */
.stApp { background: #070b13 !important; }
.stApp > header,
header[data-testid="stHeader"] {
    display: none !important;
    height: 0 !important; min-height: 0 !important; overflow: hidden !important;
    background: transparent !important;
}
section.main > div.block-container,
[data-testid="stMainBlockContainer"] {
    padding: 4px 14px 144px !important;
}
[data-testid="stAppViewContainer"],
[data-testid="stMain"] { background: #070b13 !important; }

/* 所有 Streamlit 原生控件压暗 */
div[data-testid="stVerticalBlock"] { gap: 6px !important; }
.stTextInput, .stSelectbox, .stMultiselect, .stRadio > div { gap: 4px !important; }
.st-bb, .st-at { background: transparent !important; border: none !important; }

/* 顶栏 */
.topbar {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0 0 6px 0; font-size: 11px; color: #5a6a7e;
    border-bottom: 1px solid #1a2332; margin-bottom: 10px;
}
.topbar .brand { font-size: 18px; font-weight: 700; color: #e2e8f0; letter-spacing: .5px; }

.page-head {
    display: flex; justify-content: space-between; align-items: flex-start;
    gap: 16px; padding: 2px 0 14px; margin-bottom: 12px;
    border-bottom: 1px solid #1a2332;
}
.page-head .kicker { font-size: 11px; color: #5a6a7e; letter-spacing: 3px; text-transform: uppercase; margin-bottom: 4px; }
.page-head .title { font-size: 22px; font-weight: 800; color: #f1f5f9; letter-spacing: .5px; line-height: 1.15; }
.page-head .sub { font-size: 12px; color: #64748b; margin-top: 5px; }
.page-badge {
    display: inline-flex; align-items: center; justify-content: center;
    min-height: 28px; padding: 5px 11px; border-radius: 999px;
    border: 1px solid rgba(242,54,69,.24); background: rgba(242,54,69,.08);
    color: #f87171; font-size: 12px; font-weight: 700; white-space: nowrap;
}
.page-badge.ok {
    border-color: rgba(16,185,129,.24); background: rgba(16,185,129,.08); color: #34d399;
}
.terminal-panel {
    background: rgba(15,23,36,.76); border: 1px solid #1e2a3a;
    border-radius: 8px; padding: 14px; margin-bottom: 14px;
}
.data-table-wrap {
    overflow-x: auto;
    background: rgba(15,23,36,.78);
    border: 1px solid #1e2a3a;
    border-radius: 8px;
    margin: 10px 0 14px;
}
.data-table {
    width: 100%;
    border-collapse: collapse;
    min-width: 680px;
}
.data-table.compact {
    min-width: 0;
}
.data-table.compact th,
.data-table.compact td {
    padding: 9px 10px;
    font-size: 12px;
}
.data-table th {
    color: #94a3b8;
    font-size: 11px;
    font-weight: 750;
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid #1e2a3a;
    background: rgba(255,255,255,.025);
    white-space: nowrap;
}
.data-table td {
    color: #e2e8f0;
    font-size: 13px;
    padding: 11px 12px;
    border-bottom: 1px solid rgba(30,42,58,.72);
    white-space: nowrap;
}
.data-table tbody tr:nth-child(even) td { background: rgba(255,255,255,.012); }
.data-table th.num, .data-table td.num {
    text-align: right;
    font-variant-numeric: tabular-nums;
}
.data-table td.pos { color: #f23645; font-weight: 700; }
.data-table td.neg { color: #089981; font-weight: 700; }
.data-table tr:last-child td { border-bottom: none; }
.data-table tr:hover td { background: rgba(255,255,255,.025); }
.section-title {
    margin: 16px 0 8px;
    color: #f8fafc;
    font-size: 16px;
    font-weight: 850;
}
.stock-audit-list {
    display: grid;
    grid-template-columns: 1fr;
    gap: 8px;
    margin: 10px 0 6px;
}
.stock-audit-card {
    background: linear-gradient(180deg, rgba(15,23,36,.92), rgba(10,15,26,.94));
    border: 1px solid #1e2a3a;
    border-radius: 8px;
    padding: 12px 14px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,.025);
}
.stock-audit-head {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: center;
    margin-bottom: 10px;
}
.stock-audit-title {
    color: #f8fafc;
    font-size: 15px;
    font-weight: 850;
}
.stock-audit-code {
    margin-left: 6px;
    color: #64748b;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: .6px;
}
.stock-audit-pill {
    border: 1px solid rgba(148,163,184,.18);
    border-radius: 999px;
    padding: 4px 9px;
    color: #94a3b8;
    font-size: 11px;
    font-weight: 800;
    white-space: nowrap;
}
.stock-audit-pill.up { color: #f87171; border-color: rgba(242,54,69,.28); background: rgba(242,54,69,.08); }
.stock-audit-pill.down { color: #34d399; border-color: rgba(16,185,129,.24); background: rgba(16,185,129,.07); }
.stock-audit-metrics {
    display: grid;
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 8px;
}
.stock-audit-metric {
    min-width: 0;
    background: rgba(255,255,255,.022);
    border: 1px solid rgba(30,42,58,.78);
    border-radius: 7px;
    padding: 8px 9px;
}
.stock-audit-metric .label {
    color: #64748b;
    font-size: 10px;
    font-weight: 800;
    margin-bottom: 4px;
    white-space: nowrap;
}
.stock-audit-metric .value {
    color: #e2e8f0;
    font-size: 13px;
    font-weight: 800;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.stock-audit-metric .value.pos { color: #f23645; }
.stock-audit-metric .value.neg { color: #089981; }
.competition-strip {
    display: block;
    margin: 8px 0 12px;
}
.competition-cell {
    background: rgba(15,23,36,.82);
    border: 1px solid #1e2a3a;
    border-radius: 8px;
    padding: 11px 14px;
}
.competition-cell.primary {
    background: linear-gradient(90deg, rgba(242,54,69,.16), rgba(15,23,36,.86));
    border-color: rgba(242,54,69,.24);
}
.competition-cell.ok {
    background: linear-gradient(90deg, rgba(16,185,129,.12), rgba(15,23,36,.86));
    border-color: rgba(16,185,129,.22);
}
.competition-cell .label {
    color: #64748b;
    font-size: 10px;
    font-weight: 850;
    letter-spacing: .4px;
    margin-bottom: 5px;
}
.competition-cell .value {
    color: #f8fafc;
    font-size: 17px;
    font-weight: 850;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.competition-cell .sub {
    margin-top: 3px;
    color: #94a3b8;
    font-size: 11px;
    white-space: nowrap;
}
.risk-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin: 10px 0 14px;
}
.risk-card {
    background: rgba(15,23,36,.82);
    border: 1px solid #1e2a3a;
    border-radius: 8px;
    padding: 14px 16px;
}
.risk-card .label {
    color: #94a3b8;
    font-size: 11px;
    font-weight: 750;
    letter-spacing: .4px;
}
.risk-card .value {
    color: #f8fafc;
    font-size: 20px;
    font-weight: 800;
    margin-top: 6px;
    font-variant-numeric: tabular-nums;
}
.risk-card.alert { border-color: rgba(242,54,69,.36); background: rgba(242,54,69,.08); }
.risk-card.safe { border-color: rgba(16,185,129,.30); background: rgba(16,185,129,.07); }
.risk-card.warn { border-color: rgba(245,158,11,.34); background: rgba(245,158,11,.07); }
div[data-testid="stForm"] {
    background: rgba(15,23,36,.72) !important;
    border: 1px solid #1e2a3a !important;
    border-radius: 8px !important;
    padding: 14px !important;
}
@media (max-width: 767px) {
    .page-head { padding-top: 0; margin-bottom: 10px; }
    .page-head .title { font-size: 18px; }
    .page-head .sub { display: none; }
    .page-badge { min-height: 24px; padding: 4px 9px; font-size: 11px; }
    .data-table { min-width: 560px; }
    .data-table th, .data-table td { padding: 9px 10px; font-size: 12px; }
    .stock-audit-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .stock-audit-head { align-items: flex-start; }
    .risk-grid { grid-template-columns: repeat(2, 1fr); gap: 8px; }
    .risk-card { padding: 12px; }
    .risk-card .value { font-size: 18px; }
}

/* KPI 卡片 */
.kpi-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px; }
.kpi-card {
    background: #0f1724; border-radius: 6px; padding: 14px 16px;
    border: 1px solid #1e2a3a;
}
.kpi-card .label { font-size: 10px; color: #5a6a7e; margin-bottom: 2px; letter-spacing: .5px; }
.kpi-card .value { font-size: 22px; font-weight: 700; color: #e2e8f0; font-feature-settings: "tnum"; white-space: nowrap; overflow-wrap: normal; }
.kpi-card .delta { font-size: 11px; margin-top: 1px; }
.kpi-card .delta.up { color: #f23645; }
.kpi-card .delta.down { color: #089981; }

/* 股票卡片 */
.stock-card {
    background: #0f1724; border-radius: 6px; padding: 12px 14px;
    margin-bottom: 6px; border: 1px solid #1e2a3a;
}
.stock-card .sc-header { display: flex; justify-content: space-between; align-items: center; }
.stock-card .sc-name { font-size: 14px; font-weight: 600; color: #e2e8f0; }
.stock-card .sc-pct { font-size: 13px; font-weight: 600; }
.stock-card .sc-pct.up { color: #f23645; }
.stock-card .sc-pct.down { color: #089981; }
.stock-card .sc-detail { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 12px; margin: 4px 0; font-size: 12px; color: #5a6a7e; }
.stock-card .sc-detail .val { color: #e2e8f0; font-weight: 500; }

.section-title { font-size: 13px; font-weight: 700; color: #e2e8f0; margin-bottom: 6px; }
.chart-summary { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; margin: 6px 0; }
.chart-metric { background: #0f1724; border: 1px solid #1e2a3a; border-radius: 6px; padding: 8px 10px; }
.chart-metric .label { font-size: 10px; color: #5a6a7e; }
.chart-metric .value { font-size: 14px; font-weight: 700; color: #e2e8f0; }
.chart-metric .value.up { color: #f23645; }
.chart-metric .value.down { color: #089981; }
.chart-panel { background: #0f1724; border: 1px solid #1e2a3a; border-radius: 6px; padding: 4px; }
.chart-panel .js-plotly-plot, .chart-panel .plot-container, .chart-panel .svg-container {
    background: #0b1220 !important; border-radius: 6px !important;
}
.chart-panel.pro-chart {
    background: #0b1220;
    border-color: #1e2a3a;
    padding: 0;
    overflow: hidden;
}
.chart-panel.pro-chart .js-plotly-plot,
.chart-panel.pro-chart .plot-container,
.chart-panel.pro-chart .svg-container {
    background: #ffffff !important;
    border-radius: 0 !important;
}
.boll-strip {
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
    background: #0f1724;
    border: 1px solid #1e2a3a;
    border-bottom: none;
    color: #94a3b8;
    font-size: 13px;
    padding: 7px 10px;
}
.chart-panel.pro-chart .js-plotly-plot,
.chart-panel.pro-chart .plot-container,
.chart-panel.pro-chart .svg-container {
    background: #0b1220 !important;
}
.boll-strip b { color: #e2e8f0; }

/* Scrollbar */
::-webkit-scrollbar { width: 4px; height: 4px; }
div[data-testid="stVerticalBlock"] { gap: 2px !important; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #1e2a3a; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #2a3a4e; }

/* Selectbox / Dropdown */
div[data-baseweb="select"],
div[data-baseweb="select"] > div,
div[data-baseweb="select"] > div:hover,
div[data-baseweb="select"] > div:focus,
div[data-baseweb="select"] > div:focus-within {
    background: #0f1724 !important; border: 1px solid #1e2a3a !important;
    border-radius: 6px !important; color: #e2e8f0 !important;
    box-shadow: none !important;
}
div[data-baseweb="select"] div,
div[data-baseweb="select"] span,
div[data-baseweb="select"] input {
    color: #e2e8f0 !important;
    -webkit-text-fill-color: #e2e8f0 !important;
}
div[data-baseweb="select"] svg { fill: #94a3b8 !important; color: #94a3b8 !important; }
div[data-baseweb="select"] > div:focus { border-color: #f23645 !important; }
div[data-baseweb="popover"] ul { background: #0f1724 !important; border: 1px solid #1e2a3a !important; border-radius: 6px !important; }
div[data-baseweb="popover"] li { color: #94a3b8 !important; }
div[data-baseweb="popover"] li:hover { background: rgba(255,255,255,.04) !important; color: #e2e8f0 !important; }
div[data-baseweb="popover"] li[aria-selected="true"] { background: rgba(242,54,69,.08) !important; color: #f23645 !important; }

/* Radio — 压掉小圆点 */
div[role="radiogroup"] label { display: flex !important; align-items: center !important; gap: 6px !important; }
div[role="radiogroup"] label input { accent-color: #f23645 !important; }

/* Checkbox */
div[role="checkbox"] { accent-color: #f23645 !important; }
div[data-testid="stCheckbox"] label span { color: #94a3b8 !important; }

/* Form labels */
label, .stTextInput label, .stSelectbox label, .stRadio label {
    color: #5a6a7e !important; font-size: 12px !important; font-weight: 500 !important;
}

/* Messages */
.stAlert { background: #0f1724 !important; border: 1px solid #1e2a3a !important; color: #94a3b8 !important; border-radius: 6px !important; }
.stAlert > div:first-child { color: #f23645 !important; }
.st-bk, .st-cx { color: #089981 !important; }

/* Divider */
hr { border-color: #1a2332 !important; margin: 10px 0 !important; }

/* Caption/Help text */
.stCaption, .stHelp, .stText {
    color: #5a6a7e !important; font-size: 11px !important;
}

/* Mobile nav */
.mobile-nav { margin: 0 0 8px 0; }
.mobile-nav div[role="radiogroup"] { display: flex !important; flex-wrap: wrap !important; gap: 4px !important; }
.mobile-nav div[role="radiogroup"] label {
    padding: 5px 10px !important; border-radius: 6px !important;
    font-size: 12px !important; color: #5a6a7e !important;
    background: #0f1724 !important; border: 1px solid #1e2a3a !important; cursor: pointer !important;
}
.mobile-nav div[role="radiogroup"] label[data-checked="true"] {
    background: #1e2a3a !important; color: #e2e8f0 !important; border-color: #f23645 !important;
}
.mobile-nav div[role="radiogroup"] label input { display: none !important; }
/* 顶部导航栏 */
.top-nav { display: flex; gap: 4px; margin: 0 0 8px 0; flex-wrap: wrap; }
.tn-item {
    display: flex; align-items: center; gap: 6px;
    padding: 8px 14px; border-radius: 6px;
    font-size: 13px; font-weight: 500; color: #64748b;
    background: rgba(255,255,255,.03); border: 1px solid transparent;
    cursor: pointer; transition: all .1s; white-space: nowrap;
}
.tn-item:hover { background: rgba(255,255,255,.06); color: #94a3b8; }
.tn-item.active { background: rgba(242,54,69,.08); color: #f23645; font-weight: 600; border-color: rgba(242,54,69,.15); }
.tn-item.active::before { content: ''; width: 0; }

/* 顶部导航背后的 radio — 只留最小可交互区域 */
div[role="radiogroup"]:has(#nav_top) { height: 0 !important; overflow: visible !important; padding: 0 !important; margin: 0 !important; }
div[role="radiogroup"]:has(#nav_top) label { display: inline !important; padding: 0 !important; margin: 0 !important; border: none !important; font-size: 0 !important; color: transparent !important; background: transparent !important; }
div[role="radiogroup"]:has(#nav_top) input { opacity: 0.01 !important; width: 1px !important; height: 1px !important; position: fixed !important; }

/* 移动端底部导航 */
.mob-bar-inner {
    position: fixed; bottom: 0; left: 0; right: 0; z-index: 999;
    background: #0a0f1a; border-top: 1px solid #1a2332;
    padding: 6px 8px 8px; display: flex; gap: 4px;
}
.mob-bar-inner div[data-testid="column"] { padding: 0 !important; }
.mob-bar-inner button {
    font-size: 11px !important; padding: 8px 4px !important;
    border-radius: 6px !important;
}
.desktop-only { display: none; }
.mobile-only { display: block; }

.st-key-mobile_nav_bar {
    position: fixed !important;
    left: 0 !important; right: 0 !important; bottom: 54px !important;
    z-index: 9999 !important;
    padding: 7px 8px calc(9px + env(safe-area-inset-bottom)) !important;
    background: rgba(10,15,26,.98) !important;
    border-top: 1px solid #1a2332 !important;
    box-shadow: 0 -10px 30px rgba(0,0,0,.32) !important;
}
.st-key-mobile_nav_bar [data-testid="stHorizontalBlock"] {
    display: grid !important;
    grid-template-columns: repeat(5, minmax(0, 1fr)) !important;
    gap: 6px !important;
}
.st-key-mobile_nav_bar [data-testid="column"] {
    width: 100% !important;
    min-width: 0 !important;
    padding: 0 !important;
}
.st-key-mobile_nav_bar div[data-testid="stButton"] button {
    min-height: 42px !important;
    padding: 4px 2px !important;
    border-radius: 8px !important;
    font-size: 12px !important;
    white-space: nowrap !important;
}

.st-key-desktop_nav_bar { display: none; }

@media (min-width: 768px) {
    [data-testid="stApp"]:has(.st-key-desktop_nav_bar) section[data-testid="stSidebar"] {
        display: none !important;
    }
    [data-testid="stApp"]:has(.st-key-desktop_nav_bar) [data-testid="stMain"] {
        margin-left: 300px !important;
        width: calc(100% - 300px) !important;
    }
    [data-testid="stApp"]:has(.st-key-desktop_nav_bar) [data-testid="stMainBlockContainer"] {
        max-width: none !important;
    }
    .st-key-desktop_nav_bar {
        display: flex !important;
        flex-direction: column !important;
        position: fixed !important;
        left: 0 !important;
        top: 0 !important;
        bottom: 0 !important;
        width: 300px !important;
        z-index: 9998 !important;
        padding: 24px 22px !important;
        background: #0a0f1a !important;
        border-right: 1px solid #1a2332 !important;
        box-shadow: 18px 0 44px rgba(0,0,0,.22) !important;
        overflow-y: auto !important;
    }
    .desktop-nav-brand {
        padding-bottom: 16px;
        border-bottom: 1px solid #1a2332;
        margin-bottom: 14px;
    }
    .desktop-nav-brand .name {
        font-size: 24px;
        font-weight: 850;
        line-height: 1.1;
        letter-spacing: 2px;
        background: linear-gradient(135deg, #fff4cf, #d4a853);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .desktop-nav-brand .sub {
        margin-top: 5px;
        color: #94a3b8;
        font-size: 10px;
        letter-spacing: 4px;
    }
    .desktop-nav-user {
        padding: 0 0 14px;
        border-bottom: 1px solid #1a2332;
        margin-bottom: 14px;
    }
    .desktop-nav-user .uname {
        color: #f8fafc;
        font-size: 14px;
        font-weight: 750;
    }
    .desktop-nav-user .role {
        margin-top: 4px;
        color: #94a3b8;
        font-size: 12px;
    }
    .desktop-nav-user .dot {
        display: inline-block;
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: #10b981;
        margin-right: 6px;
        vertical-align: 1px;
    }
    .desktop-nav-label {
        color: #64748b;
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 2px;
        margin: 2px 0 8px;
    }
    .st-key-desktop_nav_bar div[data-testid="stButton"] button {
        justify-content: flex-start !important;
        min-height: 42px !important;
        margin: 3px 0 !important;
        padding: 10px 14px !important;
        border-radius: 8px !important;
        color: #cbd5e1 !important;
        font-size: 14px !important;
        font-weight: 650 !important;
        border: 1px solid transparent !important;
        background: transparent !important;
    }
    .st-key-desktop_nav_bar div[data-testid="stButton"] button p,
    .st-key-desktop_nav_bar div[data-testid="stButton"] button [data-testid="stMarkdownContainer"] {
        width: 100% !important;
        color: inherit !important;
        text-align: left !important;
        font-weight: inherit !important;
    }
    .st-key-desktop_nav_bar div[data-testid="stButton"] button:hover {
        background: rgba(255,255,255,.06) !important;
        border-color: rgba(255,255,255,.06) !important;
        color: #ffffff !important;
    }
    .st-key-desktop_nav_bar div[data-testid="stButton"] button:focus,
    .st-key-desktop_nav_bar div[data-testid="stButton"] button:focus-visible,
    .st-key-desktop_nav_bar div[data-testid="stButton"] button:active {
        outline: none !important;
        box-shadow: none !important;
        background: rgba(255,255,255,.06) !important;
        border-color: rgba(255,255,255,.08) !important;
        color: #ffffff !important;
    }
    .st-key-desktop_nav_bar div[data-testid="stButton"] button[kind="primary"] {
        background: linear-gradient(90deg, rgba(242,54,69,.22), rgba(242,54,69,.08)) !important;
        border-color: rgba(242,54,69,.30) !important;
        color: #ffffff !important;
        font-weight: 800 !important;
    }
    .st-key-desktop_nav_bar div[data-testid="stButton"] button[kind="primary"]:hover,
    .st-key-desktop_nav_bar div[data-testid="stButton"] button[kind="primary"]:focus,
    .st-key-desktop_nav_bar div[data-testid="stButton"] button[kind="primary"]:focus-visible,
    .st-key-desktop_nav_bar div[data-testid="stButton"] button[kind="primary"]:active {
        background: linear-gradient(90deg, rgba(242,54,69,.28), rgba(242,54,69,.10)) !important;
        border-color: rgba(242,54,69,.34) !important;
        color: #ffffff !important;
        box-shadow: inset 0 0 0 1px rgba(242,54,69,.08) !important;
    }
    .st-key-desktop_nav_bar div[data-testid="stButton"] button *,
    .st-key-desktop_nav_bar div[data-testid="stButton"] button[kind="primary"] * {
        color: inherit !important;
        -webkit-text-fill-color: currentColor !important;
    }
    .desktop-nav-spacer { flex: 1; min-height: 18px; }
}

/* 移动端导航滚动容器 */
.mob-nav-scroll { overflow-x: auto; white-space: nowrap; -webkit-overflow-scrolling: touch; }
.mob-nav-scroll::-webkit-scrollbar { height: 2px; }
.mob-nav-scroll::-webkit-scrollbar-thumb { background: #1e2a3a; border-radius: 2px; }
.mob-nav-scroll div[data-testid="column"] { display: inline-block; float: none; min-width: fit-content; }

@media (max-width: 430px) {
    section.main > div.block-container,
    [data-testid="stMainBlockContainer"] {
        padding-bottom: 156px !important;
    }
    .st-key-mobile_nav_bar {
        bottom: 58px !important;
    }
}

@media (min-width: 768px) {
    section.main > div.block-container,
    [data-testid="stMainBlockContainer"] {
        padding: 12px 22px 28px !important;
        max-width: 1400px !important;
        margin: 0 auto !important;
    }
    .st-key-mobile_nav_bar { display: none !important; }
    .mob-bar-inner { display: none !important; }
    .kpi-grid { grid-template-columns: repeat(4, 1fr); gap: 10px; }
    .desktop-only { display: block !important; }
    .mobile-only { display: none !important; }
    .chart-summary { grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 6px; }
    .desktop-table { background: #0f1724; border-radius: 6px; padding: 0 12px 8px 12px; border: 1px solid #1e2a3a; }
    [data-testid="stDataFrame"] { border: none !important; }
    [data-testid="stDataFrame"] [data-testid="stDataFrameToolbar"] { display: none !important; }
    [data-testid="stDataFrame"] iframe,
    [data-testid="stDataFrame"] div {
        background-color: #0f1724 !important;
    }
    [data-testid="stDataFrame"] th {
        background: transparent !important; font-size: 10px !important;
        color: #5a6a7e !important; font-weight: 600 !important;
        border-bottom: 1px solid #1a2332 !important; padding: 6px 8px !important;
    }
    [data-testid="stDataFrame"] td {
        font-size: 13px !important; color: #e2e8f0 !important;
        border-bottom: 1px solid #1a2332 !important; padding: 6px 8px !important;
    }
    /* 按钮 */
    div[data-testid="stButton"] button {
        border-radius: 6px !important; font-weight: 500 !important;
        background: #1a2332 !important; border: 1px solid #1e2a3a !important;
        color: #94a3b8 !important; font-size: 13px !important; padding: 5px 14px !important;
        transition: all .1s ease !important;
    }
    div[data-testid="stButton"] button:hover { border-color: #f23645 !important; color: #e2e8f0 !important; }
    div[data-testid="stButton"] button[kind="primary"] {
        background: #f23645 !important; border: none !important; color: #fff !important;
    }
    div[data-testid="stButton"] button[kind="primary"]:hover { background: #d42d3b !important; }
    /* Expanders */
    details, .st-emotion-cache-1aej4i3 {
        background: #0f1724 !important; border: 1px solid #1e2a3a !important;
        border-radius: 6px !important; margin-bottom: 4px !important;
        color: #e2e8f0 !important;
    }
    details summary,
    details[open] summary,
    details summary:hover,
    details summary:focus,
    details summary:focus-visible,
    .st-emotion-cache-1aej4i3 summary,
    .st-emotion-cache-1aej4i3 summary:hover,
    .st-emotion-cache-1aej4i3 summary:focus,
    .st-emotion-cache-1aej4i3 summary:focus-visible {
        background: #0f1724 !important;
        font-weight: 500 !important; padding: 8px 12px !important; color: #e2e8f0 !important;
        border-radius: 6px !important;
        outline: none !important;
        box-shadow: none !important;
    }
    details summary *,
    details[open] summary *,
    .st-emotion-cache-1aej4i3 summary * {
        color: #e2e8f0 !important;
        -webkit-text-fill-color: #e2e8f0 !important;
        background: transparent !important;
    }
    /* 输入框 */
    input, div[data-baseweb="input"] input, textarea {
        background: #070b13 !important; border: 1px solid #1e2a3a !important;
        color: #e2e8f0 !important; border-radius: 6px !important;
        padding: 6px 10px !important; font-size: 13px !important;
    }
    input:focus, div[data-baseweb="input"] input:focus {
        border-color: #f23645 !important; box-shadow: 0 0 0 2px rgba(242,54,69,.1) !important;
    }
    /* Number input */
    div[data-baseweb="input"] button { background: #1a2332 !important; border-color: #1e2a3a !important; color: #94a3b8 !important; }
    /* File uploader */
    div[data-testid="stFileUploader"] { background: #0f1724 !important; border: 1px dashed #1e2a3a !important; border-radius: 6px !important; }
}

@media (min-width: 768px) {
    [data-testid="stSidebarNav"] { display: none !important; }
}
</style>
"""

SIDEBAR_CSS = """
<style>
    section[data-testid="stSidebar"] {
        background: #0a0f1a !important;
        border-right: 1px solid #1a2332 !important;
        min-width: 300px !important;
    }
    [data-testid="stSidebarNav"] { display: none !important; }
    [data-testid="stStatusWidget"] { display: none !important; }
    .stDeployButton, footer, #MainMenu, [data-testid="stToolbar"],
    [data-testid="stDecoration"], [data-testid="manage-app-button"] { display: none !important; }
    section[data-testid="stSidebar"]::-webkit-scrollbar { width: 3px; background: transparent; }
    section[data-testid="stSidebar"]::-webkit-scrollbar-thumb { background: #1e2a3a; border-radius: 3px; }

    .sb-brand { padding: 18px 20px 14px 20px; border-bottom: 1px solid #1a2332; }
    .sb-brand .name,
    .sb-brand .name p {
        font-size: 24px !important; font-weight: 850 !important; margin: 0 !important;
        letter-spacing: 2px !important; line-height: 1.1 !important;
        background: linear-gradient(135deg, #fff4cf, #d4a853) !important;
        -webkit-background-clip: text !important; -webkit-text-fill-color: transparent !important; background-clip: text !important;
    }
    .sb-brand .sub,
    .sb-brand .sub p {
        color: #94a3b8 !important; font-size: 10px !important; letter-spacing: 4px !important; margin: 5px 0 0 0 !important;
    }
    .sb-user { padding: 12px 20px 12px 20px; border-bottom: 1px solid #1a2332; }
    .sb-user .uname,
    .sb-user .uname p { font-size: 14px !important; font-weight: 700 !important; color: #f8fafc !important; margin: 0 !important; }
    .sb-user .urole,
    .sb-user .urole p { font-size: 12px !important; color: #94a3b8 !important; margin: 4px 0 0 0 !important; }
    .sb-user .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #089981; margin-right: 4px; vertical-align: middle; }
    .menu-group-label { padding: 14px 20px 6px 20px; }
    .menu-group-label,
    .menu-group-label p { font-size: 10px !important; font-weight: 800 !important; color: #64748b !important; text-transform: uppercase; letter-spacing: 2px !important; margin: 0 !important; }

    /* 侧边栏导航按钮 */
    section[data-testid="stSidebar"] button {
        display: flex !important; align-items: center !important; justify-content: flex-start !important;
        width: calc(100% - 16px) !important;
        margin: 4px 8px !important; padding: 10px 14px !important;
        border-radius: 8px !important; font-size: 14px !important;
        font-weight: 650 !important; text-align: left !important;
        background: transparent !important; border: 1px solid transparent !important;
        color: #cbd5e1 !important; cursor: pointer !important;
        transition: background .1s !important;
    }
    section[data-testid="stSidebar"] button [data-testid="stMarkdownContainer"],
    section[data-testid="stSidebar"] button p {
        width: 100% !important;
        color: inherit !important;
        text-align: left !important;
        font-weight: inherit !important;
    }
    section[data-testid="stSidebar"] button:hover {
        background: rgba(255,255,255,.06) !important; color: #ffffff !important;
        border-color: rgba(255,255,255,.06) !important;
    }
    section[data-testid="stSidebar"] button:focus,
    section[data-testid="stSidebar"] button:focus-visible,
    section[data-testid="stSidebar"] button:active {
        outline: none !important;
        box-shadow: none !important;
        background: rgba(255,255,255,.06) !important;
        color: #ffffff !important;
        border-color: rgba(255,255,255,.08) !important;
    }
    section[data-testid="stSidebar"] button[kind="primary"] {
        background: linear-gradient(90deg, rgba(242,54,69,.22), rgba(242,54,69,.08)) !important;
        color: #ffffff !important;
        border-color: rgba(242,54,69,.30) !important;
        font-weight: 800 !important; position: relative !important;
        box-shadow: inset 0 0 0 1px rgba(242,54,69,.08) !important;
    }
    section[data-testid="stSidebar"] button[kind="primary"]:hover,
    section[data-testid="stSidebar"] button[kind="primary"]:focus,
    section[data-testid="stSidebar"] button[kind="primary"]:focus-visible,
    section[data-testid="stSidebar"] button[kind="primary"]:active {
        background: linear-gradient(90deg, rgba(242,54,69,.28), rgba(242,54,69,.10)) !important;
        color: #ffffff !important;
        border-color: rgba(242,54,69,.34) !important;
        box-shadow: inset 0 0 0 1px rgba(242,54,69,.08) !important;
    }
    section[data-testid="stSidebar"] button *,
    section[data-testid="stSidebar"] button[kind="primary"] * {
        color: inherit !important;
        -webkit-text-fill-color: currentColor !important;
    }
    section[data-testid="stSidebar"] button[kind="primary"]::before {
        content: '' !important; position: absolute !important;
        left: 0 !important; top: 6px !important; bottom: 6px !important;
        width: 3px !important; background: #f23645 !important;
        border-radius: 0 2px 2px 0 !important;
    }
    section[data-testid="stSidebar"] button[kind="secondary"] { border: none !important; background: transparent !important; }
    section[data-testid="stSidebar"] button:last-of-type {
        margin: 4px 12px !important; width: calc(100% - 24px) !important;
    }
    @media (min-width: 768px) {
        [data-testid="stSidebarCollapseButton"] { display: none !important; }
    }
</style>
"""

DASHBOARD_CSS = """
<style>
    .stApp { background: #080c17 !important; }
    section[data-testid="stSidebar"] { display: none !important; }
    section.main > div.block-container,
    [data-testid="stMainBlockContainer"] { padding: 0 14px 0 14px !important; max-width: 1400px !important; margin: 0 auto !important; }
    #MainMenu, .stDeployButton, footer, [data-testid="stStatusWidget"],
    [data-testid="stDecoration"], [data-testid="stToolbar"], header { display: none !important; }
    .stApp > header { height: 0 !important; overflow: hidden; }

    .dash-top { display: flex; justify-content: space-between; align-items: center; padding: 8px 0 16px 0; }
    .dash-brand { font-size: 22px; font-weight: 800; letter-spacing: 4px;
        background: linear-gradient(135deg, #f0e6d3, #d4a853);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
        white-space: nowrap; }
    .dash-sub { font-size: 10px; color: rgba(255,255,255,.25); letter-spacing: 3px; text-transform: uppercase; white-space: nowrap; }
    .dash-clock { font-size: 13px; color: rgba(255,255,255,.35); font-family: monospace; letter-spacing: 1px; }

    .mkt-bar { display: flex; align-items: center; gap: 10px; padding: 10px 16px;
        background: rgba(255,255,255,.03); border: 1px solid rgba(255,255,255,.06);
        border-radius: 10px; margin-bottom: 18px; }
    .mkt-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
    .mkt-dot.open { background: #10b981; box-shadow: 0 0 8px rgba(16,185,129,.5); }
    .mkt-dot.closed { background: #ef4444; box-shadow: 0 0 8px rgba(239,68,68,.5); }
    .mkt-text { font-size: 13px; color: rgba(255,255,255,.5); }
    .mkt-text strong { color: rgba(255,255,255,.9); }
    .mkt-round { margin-left: auto; font-size: 12px; color: rgba(255,255,255,.25); font-family: monospace; }

    .stock-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 8px; margin-bottom: 10px; }
    .s-card {
        background: linear-gradient(135deg, rgba(255,255,255,.04), rgba(255,255,255,.01));
        border: 1px solid rgba(255,255,255,.08); border-radius: 10px;
        padding: 10px 12px 12px; position: relative; overflow: hidden; transition: border-color .2s;
        cursor: default;
    }
    .s-card:hover { border-color: rgba(255,255,255,.15); }
    .s-card .sym { font-size: 9px; color: rgba(255,255,255,.35); letter-spacing: 1px; text-transform: uppercase; }
    .s-card .nm { font-size: 13px; font-weight: 600; color: rgba(255,255,255,.85); margin: 2px 0 5px 0; }
    .s-card .pr { font-size: 21px; font-weight: 700; font-feature-settings: "tnum"; white-space: nowrap; overflow-wrap: normal; }
    .s-card .pr.up { color: #ef5350; } .s-card .pr.down { color: #2ecc71; }
    .s-card .chg { font-size: 13px; margin-top: 2px; font-weight: 500; }
    .s-card .chg.up { color: #ef5350; } .s-card .chg.down { color: #2ecc71; }
    .s-card .extra { font-size: 11px; color: rgba(255,255,255,.25); margin-top: 6px; font-family: monospace; }
    .s-meta { display: none; grid-template-columns: 1fr 1fr; gap: 4px 10px; margin-top: 10px; }
    .s-meta span { color: rgba(255,255,255,.28); font-size: 11px; line-height: 1.35; font-family: monospace; min-width: 0; }
    .s-meta b { color: rgba(255,255,255,.58); font-weight: 600; }
    .s-card::after { content: ''; position: absolute; bottom: 0; left: 12px; right: 12px; height: 2px; border-radius: 2px 2px 0 0; }
    .s-card.up::after { background: #ef5350; }
    .s-card.down::after { background: #2ecc71; }

    .tab-row { display: flex; gap: 6px; margin-bottom: 12px; }
    .tab-btn { padding: 6px 18px; border-radius: 8px; font-size: 13px; font-weight: 500;
        cursor: pointer; border: 1px solid rgba(255,255,255,.08); background: transparent;
        color: rgba(255,255,255,.4); transition: all .15s; font-family: inherit; }
    .tab-btn:hover { background: rgba(255,255,255,.05); color: rgba(255,255,255,.7); }
    .tab-btn.active { background: rgba(59,130,246,.15); border-color: rgba(59,130,246,.3); color: #60a5fa; }
    .chart-box { background: rgba(255,255,255,.02); border: 1px solid rgba(255,255,255,.06);
        border-radius: 12px; padding: 8px; margin-bottom: 12px; }
    .dash-chart-head {
        display: flex; justify-content: space-between; align-items: end;
        gap: 12px; margin: 2px 0 10px;
    }
    .dash-chart-head .name { font-size: 16px; font-weight: 750; color: rgba(255,255,255,.86); }
    .dash-chart-head .meta { font-size: 11px; color: rgba(255,255,255,.32); font-family: monospace; }
    .dash-ft { display: flex; justify-content: space-between; padding: 10px 0 0 0;
        border-top: 1px solid rgba(255,255,255,.06); margin-top: 4px;
        font-size: 11px; color: rgba(255,255,255,.18); font-family: monospace; }

    .login-btn { padding: 8px 20px; border-radius: 8px; font-size: 14px; font-weight: 600;
        cursor: pointer; border: 1px solid rgba(212,168,83,.3); background: rgba(212,168,83,.1);
        color: #d4a853; font-family: inherit; text-decoration: none; transition: all .15s;
        display: inline-block; text-align: center; }
    .login-btn:hover { background: rgba(212,168,83,.2); color: #f0e6d3; border-color: rgba(212,168,83,.5); }

    @media (max-width: 768px) {
        [data-testid="stMainBlockContainer"] { padding-bottom: 92px !important; }
        .stock-grid {
            display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px;
            margin-bottom: 10px; scroll-snap-type: x mandatory;
        }
        .s-card {
            flex: 0 0 138px; min-width: 138px; padding: 10px 12px 12px;
            border-radius: 10px; scroll-snap-align: start;
        }
        .s-card .sym { font-size: 9px; }
        .s-card .nm { font-size: 12px; margin-bottom: 4px; }
        .s-card .pr { font-size: 18px; letter-spacing: 0; }
        .s-card .chg { font-size: 10px; }
        .s-meta { display: none; }
        .s-card::after { left: 12px; right: 12px; }
        .tab-row { gap: 4px; margin-bottom: 6px; overflow-x: auto; }
        .dash-chart-head { align-items: flex-start; flex-direction: column; gap: 2px; }
        .dash-ft { padding-bottom: 64px; }
    }
    @media (max-width: 390px) {
        .s-card .pr { font-size: 19px; }
        .s-card { padding-left: 12px; padding-right: 12px; }
    }
</style>
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 公开行情大屏（无需登录）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def page_public_dashboard():
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)
    stocks, quotes = get_public_quote_snapshot()
    if not stocks:
        st.markdown('<div style="color:rgba(255,255,255,.3);text-align:center;padding:40px;">暂无行情数据</div>', unsafe_allow_html=True)
        return

    # 市场状态
    mkt_open = is_market_open()
    mkt_round = get_market_round()
    mkt_cls = "open" if mkt_open else "closed"
    mkt_text = "交易中" if mkt_open else "已闭市"

    # 顶栏（实时时钟用 JS 走浏览器时间）
    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown(f'<div style="display:flex;align-items:baseline;gap:12px;"><span class="dash-brand">Gipfel</span><span class="dash-sub">智能投资分析系统</span></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div style="text-align:right;"><span class="dash-clock" id="liveClock"></span></div>', unsafe_allow_html=True)
        if st.button("登录交易", key="dash_login_btn", type="primary", use_container_width=True):
            st.session_state.show_login = True

    # 市场状态条
    st.markdown(f'<div class="mkt-bar"><span class="mkt-dot {mkt_cls}"></span><span class="mkt-text">市场 <strong>{mkt_text}</strong> ｜ 第 <strong>{mkt_round}</strong> 轮</span><span class="mkt-round" id="liveClockMkt"></span></div>', unsafe_allow_html=True)
    render_competition_strip()

    # JS 实时时钟（每秒更新，不依赖服务端）
    st.markdown("""
    <script>
        function updateClocks(){
            var d=new Date();
            var s=d.getFullYear()+'/'+String(d.getMonth()+1).padStart(2,'0')+'/'+String(d.getDate()).padStart(2,'0')+' '+d.toLocaleTimeString('zh-CN',{hour12:false});
            var e1=document.getElementById('liveClock');if(e1)e1.textContent=s;
            var e2=document.getElementById('liveClockMkt');if(e2)e2.textContent='⏱ '+s;
        }
        updateClocks();
        setInterval(updateClocks,1000);
    </script>
    """, unsafe_allow_html=True)

    # 四只股票行情卡片
    cards = ""
    for s in stocks:
        q = quotes.get(s["symbol"]) or {"price": s["current_price"], "ref_price": s["previous_close"] or s["current_price"], "change": 0, "pct": 0, "volume5": 0, "buy5": 0, "sell5": 0, "round": "-"}
        p = q["price"]
        prev = q["ref_price"]
        chg = q["change"]
        pct = q["pct"]
        cls = "up" if chg >= 0 else "down"
        sign = "+" if chg >= 0 else ""
        cards += f"""
        <div class="s-card {cls}">
            <div class="sym">{s['symbol']}</div>
            <div class="nm">{esc(s['name'])}</div>
            <div class="pr {cls}">{fmt_money_short(p)}</div>
            <div class="chg {cls}">{sign}{chg:,.2f} ({sign}{pct:.2f}%)</div>
            <div class="s-meta">
                <span>当前 <b>第 {mkt_round} 轮</b></span>
                <span>成交 <b>第 {q['round']} 轮</b></span>
                <span>参考 <b>{fmt_money_short(prev)}</b></span>
                <span>量 <b>{q['volume5']:,}</b></span>
                <span>买额 <b>{fmt_money_short(q['buy5'])}</b></span>
                <span>卖额 <b>{fmt_money_short(q['sell5'])}</b></span>
            </div>
        </div>"""
    st.markdown(f'<div class="stock-grid">{cards}</div>', unsafe_allow_html=True)

    st.markdown('<div class="tab-row">', unsafe_allow_html=True)
    cols = st.columns([1] * len(stocks))
    if "dash_sym" not in st.session_state:
        st.session_state.dash_sym = stocks[0]["symbol"]
    for i, s in enumerate(stocks):
        active = "active" if st.session_state.dash_sym == s["symbol"] else ""
        btn_type = "primary" if active else "secondary"
        if cols[i].button(f"{s['name']}", key=f"tab_{s['symbol']}", type=btn_type, use_container_width=True):
            st.session_state.dash_sym = s["symbol"]
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    # K线图
    sym = st.session_state.dash_sym
    selected_stock = next((s for s in stocks if s["symbol"] == sym), stocks[0])
    data = get_kline_data(sym)
    if data:
        raw_k = pd.DataFrame(data).sort_values("round").reset_index(drop=True)
        first_open = float(raw_k.iloc[0]["open_price"])
        df_k = build_professional_kline_view(raw_k, sym)
        df_k["x_pos"] = df_k["display_round"]
        df_k["x_label"] = df_k["display_round"].apply(lambda r: f"{int(r)}")
        x_values = df_k["x_pos"]

        RED_UP = "#d64b45"
        GREEN_DN = "#07984f"
        up_mask = df_k["close_price"] >= df_k["open_price"]
        body_fill = ["rgba(255,255,255,0)" if u else GREEN_DN for u in up_mask]
        candle_line = [RED_UP if u else GREEN_DN for u in up_mask]
        vol_fill = ["rgba(255,255,255,0)" if u else GREEN_DN for u in up_mask]
        vol_line = [RED_UP if u else GREEN_DN for u in up_mask]

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25])
        latest_upper = float(df_k["upper"].dropna().iloc[-1]) if df_k["upper"].notna().any() else float(df_k["high_price"].max())
        latest_lower = float(df_k["lower"].dropna().iloc[-1]) if df_k["lower"].notna().any() else float(df_k["low_price"].min())
        latest_close = float(df_k.iloc[-1]["close_price"])
        high_idx = int(df_k["high_price"].idxmax())
        low_idx = int(df_k["low_price"].idxmin())
        high_price = float(df_k.loc[high_idx, "high_price"])
        low_price = float(df_k.loc[low_idx, "low_price"])
        fig.add_annotation(x=df_k.loc[high_idx, "x_pos"], y=high_price, text=f"高 {high_price:,.2f}",
            showarrow=True, arrowhead=2, arrowsize=0.8, arrowwidth=1,
            arrowcolor="#94a3b8", font=dict(size=13, color="#e2e8f0"),
            bgcolor="rgba(15,23,36,.82)", bordercolor="#1e2a3a", row=1, col=1)
        fig.add_annotation(x=df_k.loc[low_idx, "x_pos"], y=low_price, text=f"低 {low_price:,.2f}",
            showarrow=True, arrowhead=2, arrowsize=0.8, arrowwidth=1,
            arrowcolor="#94a3b8", font=dict(size=13, color="#e2e8f0"),
            bgcolor="rgba(15,23,36,.82)", bordercolor="#1e2a3a", row=1, col=1)

        latest_mid = float(df_k["mid"].dropna().iloc[-1]) if df_k["mid"].notna().any() else float(df_k.iloc[-1]["close_price"])
        latest_upper = float(df_k["upper"].dropna().iloc[-1]) if df_k["upper"].notna().any() else float(df_k["high_price"].max())
        latest_lower = float(df_k["lower"].dropna().iloc[-1]) if df_k["lower"].notna().any() else float(df_k["low_price"].min())
        st.markdown(f"""
        <div class="dash-chart-head">
            <div>
                <div class="name">{esc(selected_stock["name"])} · {esc(selected_stock["symbol"])}</div>
                <div class="meta">BOLL [20,2] ｜ MID {latest_mid:,.2f} ｜ UPPER {latest_upper:,.2f} ｜ LOWER {latest_lower:,.2f}</div>
            </div>
            <div class="meta">红涨绿跌 · BOLL/MA</div>
        </div>
        """, unsafe_allow_html=True)

        tick_step = max(1, len(df_k)//6)
        tick_vals = x_values.iloc[::tick_step]
        tick_text = df_k["x_label"].iloc[::tick_step]
        y_range, _, _, _ = kline_display_range(df_k, first_open)
        y_ticks = np.linspace(y_range[0], y_range[1], 6)
        pct_text = [f"{((v - first_open) / first_open * 100):+.2f}%" if first_open else "0.00%" for v in y_ticks]
        vol_max = float(df_k["volume"].max() or 1)
        vol_ticks = np.linspace(0, vol_max, 4)
        fig.update_layout(height=600, plot_bgcolor="#0b1220", paper_bgcolor="#0b1220",
            margin=dict(t=24, b=8, l=56, r=56), xaxis_rangeslider_visible=False,
            font=dict(color="#94a3b8", size=10), hovermode="x unified",
            hoverlabel=dict(bgcolor="#111827", font_size=12, font_color="#e5e7eb", bordercolor="#334155"),
            xaxis=dict(type="linear", showspikes=True, spikemode="across", spikethickness=0.8, spikecolor="#64748b", spikedash="solid"),
            yaxis=dict(showspikes=True, spikethickness=0.8, spikecolor="#64748b", spikedash="solid"),
            showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                bgcolor="rgba(15,23,36,0.86)", bordercolor="#1e2a3a", borderwidth=0.5,
                font=dict(color="#cbd5e1", size=10)),
            bargap=0.42)
        fig.add_hline(y=latest_close, line_width=1, line_dash="dot", line_color="#fbbf24",
            annotation_text=f"最新 {latest_close:,.2f}", annotation_position="top right",
            annotation_font_color="#fbbf24", row=1, col=1)
        fig.add_hline(y=first_open, line_width=7, line_dash="solid", line_color="rgba(148,163,184,.22)",
            row=1, col=1)
        fig.update_yaxes(showgrid=True, gridcolor="#1e2a3a", griddash="dot",
            range=y_range, tickmode="array", tickvals=y_ticks, ticktext=[fmt_axis_num(v) for v in y_ticks],
            side="right", row=1, col=1, zeroline=False, tickfont=dict(size=12, color="#94a3b8", family="monospace"))
        fig.update_layout(yaxis3=dict(
            overlaying="y", anchor="x", side="left", range=y_range,
            tickmode="array", tickvals=y_ticks, ticktext=pct_text,
            showgrid=False, zeroline=False, ticks="outside",
            tickfont=dict(size=12, color="#94a3b8", family="monospace"),
            title=dict(text="", font=dict(size=10, color="#94a3b8")),
        ))
        fig.update_xaxes(showgrid=False, type="linear", tickmode="array", tickvals=tick_vals, ticktext=tick_text, row=1, col=1)
        fig.update_yaxes(showgrid=True, gridcolor="#1e2a3a", griddash="dot",
            tickmode="array", tickvals=vol_ticks, ticktext=[fmt_axis_num(v) for v in vol_ticks],
            side="right", row=2, col=1, zeroline=False, tickfont=dict(size=9, color="#94a3b8"))
        fig.update_xaxes(showgrid=False, type="linear", tickmode="array", tickvals=tick_vals, ticktext=tick_text, row=2, col=1)

        st.markdown('<div class="chart-panel pro-chart">', unsafe_allow_html=True)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False, "scrollZoom": True})
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="color:rgba(255,255,255,.3);text-align:center;padding:30px;">暂无K线数据</div>', unsafe_allow_html=True)

    # 底部
    st.markdown(f'<div class="dash-ft"><span>Gipfel · 智能投资分析系统</span><span>数据每5秒刷新 · 仅供模拟参考</span></div>', unsafe_allow_html=True)

    # 自动刷新（仅公开行情页，不影响登录态）
    col1, col2, col3 = st.columns([3, 2, 3])
    with col2:
        if st.button("🔄 刷新", use_container_width=True):
            st.rerun()
    st.markdown("""
    <div style="text-align:center;color:rgba(255,255,255,.08);font-size:10px;font-family:monospace;">点击「🔄 刷新」手动更新数据</div>
    """, unsafe_allow_html=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 登录页
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def page_login():
    """登录页 — 金色奢华风格"""
    st.markdown("""
    <style>
        .stApp {
            background: linear-gradient(135deg, #060b1a 0%, #0d1a3a 50%, #060b1a 100%) !important;
        }
        section[data-testid="stSidebar"] { display: none !important; }
        .stApp > header { height: 0 !important; overflow: hidden; }
        div[data-testid="stToolbar"] { visibility: hidden; }
        section.main > div.block-container,
        [data-testid="stMainBlockContainer"] { padding: 0 !important; }
        div[data-testid="stTextInput"] input {
            background: rgba(10,18,40,.85) !important;
            border: 1px solid rgba(255,255,255,.1) !important;
            color: #e8edf5 !important;
            border-radius: 10px !important;
            padding: 16px 18px !important;
            font-size: 15px !important;
            transition: all .25s ease !important;
        }
        input::placeholder { color: rgba(255,255,255,.2) !important; }
        div[data-testid="stTextInput"] input:focus {
            border-color: rgba(212,168,83,.4) !important;
            box-shadow: 0 0 24px rgba(212,168,83,.08) !important;
        }
        button[aria-label="Show password text"],
        button[aria-label="Hide password text"] {
            font-size: 0 !important;
            color: transparent !important;
        }
        button[aria-label="Show password text"]::after {
            content: "显示" !important;
            font-size: 12px !important;
            color: rgba(255,255,255,.45) !important;
        }
        button[aria-label="Hide password text"]::after {
            content: "隐藏" !important;
            font-size: 12px !important;
            color: rgba(255,255,255,.45) !important;
        }
        div[data-testid="stButton"] button[kind="primary"] {
            background: linear-gradient(135deg, #d4a853, #f0e6d3) !important;
            color: #0a0e1a !important; border: none !important;
            box-shadow: 0 4px 20px rgba(212,168,83,.25) !important;
            border-radius: 10px !important;
            padding: 14px !important;
            font-weight: 600 !important;
        }
        div[data-testid="stButton"] button[kind="primary"]:hover {
            box-shadow: 0 6px 28px rgba(212,168,83,.35) !important;
        }
        div[data-testid="stButton"] button[kind="secondary"] {
            background: transparent !important;
            border: 1px solid rgba(255,255,255,.1) !important;
            color: rgba(255,255,255,.4) !important;
            border-radius: 8px !important;
        }
        .stAlert {
            background: rgba(255,255,255,.03) !important;
            border: 1px solid rgba(255,255,255,.06) !important;
            color: rgba(255,255,255,.6) !important;
            border-radius: 8px !important;
        }
    </style>""", unsafe_allow_html=True)

    st.markdown("<div style='height:12vh'></div>", unsafe_allow_html=True)
    
    st.markdown("""
    <div style="text-align:center;margin-bottom:32px;">
        <h1 style="font-size:52px;font-weight:800;letter-spacing:8px;margin:0;
            background:linear-gradient(135deg,#f0e6d3,#d4a853);
            -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;">Gipfel</h1>
        <p style="color:rgba(255,255,255,.2);font-size:11px;letter-spacing:6px;text-transform:uppercase;margin:16px 0 0 0;">智能投资分析系统</p>
    </div>""", unsafe_allow_html=True)

    if st.session_state.get("login_error"):
        st.error(st.session_state.login_error)
        st.session_state.login_error = ""
    if st.session_state.get("login_ok"):
        st.success(st.session_state.login_ok)
        st.balloons()
        st.session_state.login_ok = ""

    with st.form("login_form"):
        st.text_input("用户名", placeholder="请输入用户名", label_visibility="collapsed", key="login_u")
        st.text_input("密码", type="password", placeholder="请输入密码", label_visibility="collapsed", key="login_p")
        if st.form_submit_button("登 录", type="primary", use_container_width=True):
            u = st.session_state.get("login_u", ""); p = st.session_state.get("login_p", "")
            if not u or not p: st.session_state.login_error = "请输入用户名和密码"
            else:
                ok, role = auth_user(u, p)
                if ok:
                    st.session_state.logged_in = True; st.session_state.username = u; st.session_state.role = role
                    st.session_state.nav_current = "市场控制" if role == "admin" else "总览"
                    log_action(u, "login", "auth", "success")
                else:
                    with get_db_cm() as conn:
                        cnt = conn.execute("SELECT COUNT(*) FROM login_attempts WHERE username=? AND attempt_time > datetime('now', '-30 seconds')", (u,)).fetchone()[0]
                    if cnt >= 5: st.session_state.login_error = "密码错误次数过多，请30秒后再试"
                    else: st.session_state.login_error = f"用户名或密码错误（剩余{5-cnt}次）"
            st.rerun()

    if st.button("← 返回行情看板", key="back_to_dash"):
        st.session_state.show_login = False
        st.rerun()
    st.markdown('<p style="text-align:center;color:rgba(255,255,255,.1);font-size:10px;margin-top:20px;">Gipfel · 智能投资分析系统</p>', unsafe_allow_html=True)

def fmt_money(v):   return f"¥{v:,.0f}"
def fmt_pnl(v):     return f"¥{v:,.2f}"
def fmt_money_short(v):
    v = float(v or 0)
    if abs(v) >= 10000:
        return f"¥{v / 10000:,.2f}万"
    return fmt_money(v)
def fmt_pct(v, s=True):
    sign = "+" if (s and v > 0) else ("" if s else "")
    return f"{sign}{v:,.2f}%"

def fmt_num(v):     return f"{v:,}"

def fmt_axis_num(v):
    v = float(v or 0)
    av = abs(v)
    if av >= 100000000:
        return f"{v / 100000000:.2f}亿"
    if av >= 10000:
        return f"{v / 10000:.2f}万"
    if av < 1000:
        return f"{v:.2f}"
    return f"{v:,.0f}"

def page_header(title, subtitle="", badge=None, ok=False):
    badge_html = ""
    if badge:
        badge_cls = "page-badge ok" if ok else "page-badge"
        badge_html = f'<span class="{badge_cls}">{esc(badge)}</span>'
    sub_html = f'<div class="sub">{esc(subtitle)}</div>' if subtitle else ""
    st.markdown(f"""
    <div class="page-head">
        <div>
            <div class="kicker">Gipfel INSIGHT+</div>
            <div class="title">{esc(title)}</div>
            {sub_html}
        </div>
        {badge_html}
    </div>
    """, unsafe_allow_html=True)

def kpi_card(label, value, delta=None, up=True):
    delta_html = ""
    if delta is not None:
        delta_cls = "up" if up else "down"
        delta_html = f'<div class="delta {delta_cls}">{esc(delta)}</div>'
    return f'<div class="kpi-card"><div class="label">{esc(label)}</div><div class="value">{esc(value)}</div>{delta_html}</div>'

def render_competition_strip():
    snap = get_competition_snapshot()
    state_cls = "ok" if snap["ok"] else ""
    st.markdown(f"""
    <div class="competition-strip">
        <div class="competition-cell primary {state_cls}">
            <div class="label">竞赛状态</div>
            <div class="value">第 {snap["round"]} 轮 · {esc(snap["state"])}</div>
            <div class="sub">实时撮合 · 轮次结算 · 赛程监控</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_table(df, columns=None, compact=False):
    if df is None or len(df) == 0:
        st.info("暂无数据")
        return
    view = df[columns].copy() if columns else df.copy()
    numeric_keys = ("价", "额", "量", "率", "成本", "市值", "盈亏", "收益", "轮次", "数量", "持仓", "用户数", "PE", "碳排", "幸福度", "price")

    def cell_class(col, value):
        col_s = str(col)
        value_s = str(value)
        classes = []
        if any(k in col_s for k in numeric_keys):
            classes.append("num")
        if any(k in col_s for k in ("盈亏", "收益率", "涨跌幅")):
            clean_value = value_s.strip().replace("¥", "").replace(",", "")
            if clean_value.startswith("-"):
                classes.append("neg")
            elif clean_value not in ("0", "0.00", "0.00%"):
                classes.append("pos")
        return f' class="{" ".join(classes)}"' if classes else ""

    head = "".join(f"<th{cell_class(c, '')}>{esc(str(c))}</th>" for c in view.columns)
    rows = []
    for _, row in view.iterrows():
        rows.append("<tr>" + "".join(f"<td{cell_class(c, row[c])}>{esc(str(row[c]))}</td>" for c in view.columns) + "</tr>")
    table_cls = "data-table compact" if compact else "data-table"
    st.markdown(f"""
    <div class="data-table-wrap">
        <table class="{table_cls}">
            <thead><tr>{head}</tr></thead>
            <tbody>{''.join(rows)}</tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)

def render_admin_risk_panel():
    df, warn_df = get_admin_risk_overview()
    if df.empty:
        return
    risky_count = 0 if (len(warn_df) == 1 and warn_df.iloc[0]["级别"] == "正常") else len(warn_df)
    cash_low = int((df["余额"] <= 1000).sum())
    max_conc = float(df["集中度"].max()) if not df.empty else 0
    active_trades = int(df["本轮交易"].sum()) if not df.empty else 0
    risk_cls = "safe" if risky_count == 0 else "alert"
    cash_cls = "safe" if cash_low == 0 else "warn"
    conc_cls = "safe" if max_conc < 80 else "warn"
    st.markdown(f"""
    <div class="risk-grid">
        <div class="risk-card {risk_cls}"><div class="label">风险信号</div><div class="value">{fmt_num(risky_count)}</div></div>
        <div class="risk-card {cash_cls}"><div class="label">现金耗尽</div><div class="value">{fmt_num(cash_low)}</div></div>
        <div class="risk-card {conc_cls}"><div class="label">最高集中度</div><div class="value">{max_conc:,.2f}%</div></div>
        <div class="risk-card"><div class="label">本轮交易</div><div class="value">{fmt_num(active_trades)}</div></div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""<div style="font-size:14px;font-weight:700;color:#eef2ff;margin:12px 0 8px;">风险监控</div>""", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.2, 1, 1])
    with c1:
        render_table(warn_df.head(8), compact=True)
    with c2:
        leaders = df.sort_values("总资产", ascending=False).head(5).copy()
        leaders["总资产"] = leaders["总资产"].apply(lambda x: f"¥{x:,.0f}")
        leaders["收益率"] = leaders["收益率"].apply(lambda x: f"{x:,.2f}%")
        render_table(leaders[["选手", "总资产", "收益率"]], compact=True)
    with c3:
        losses = df[df["浮动盈亏"] < 0].sort_values("浮动盈亏").head(5).copy()
        if losses.empty:
            render_table(pd.DataFrame([{"选手": "全场", "浮动盈亏": "暂无亏损", "收益率": "-", "集中度": "-"}]), compact=True)
        else:
            losses["浮动盈亏"] = losses["浮动盈亏"].apply(lambda x: f"¥{x:,.0f}")
            losses["收益率"] = losses["收益率"].apply(lambda x: f"{x:,.2f}%")
            losses["集中度"] = losses["集中度"].apply(lambda x: f"{x:,.2f}%")
            render_table(losses[["选手", "浮动盈亏", "收益率", "集中度"]], compact=True)

def download_db_button():
    """数据导出（PostgreSQL 不支持文件下载，改为表数据导出）"""
    st.caption("数据库已迁移到 PostgreSQL，数据持久保存。如需备份可在 Neon 控制台操作。")

GREEN = "#16a34a"; RED = "#ef4444"

def pnl_class(v): return "up" if v >= 0 else "down"
def pnl_color(v): return "#f23645" if v >= 0 else "#089981"  # 红涨/绿跌（A股标准）

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 页面：总览
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def page_overview():
    data = get_user_overview(st.session_state.username)
    bal = get_user_balance(st.session_state.username)
    mv = data["total_assets"]
    total = mv + bal
    page_header("总览", "资产、余额与收益状态", badge=st.session_state.username, ok=True)
    render_competition_strip()

    # KPI卡片：总资产拆明细
    css_col2 = "grid-template-columns:repeat(2,1fr)!important;" if st.session_state.get('mobile', False) else ""
    st.markdown(f"""
    <div class="kpi-grid" style="{css_col2}">
        <div class="kpi-card"><div class="label">总资产</div><div class="value">{fmt_money(total)}</div><div class="delta {"up" if data["total_pnl"]>=0 else "down"}">持仓 {fmt_money(mv)} ｜ 余额 {fmt_money(bal)}</div></div>
        <div class="kpi-card"><div class="label">可用余额</div><div class="value">{fmt_money(bal)}</div></div>
        <div class="kpi-card"><div class="label">持仓盈亏</div><div class="value">{fmt_money(data["total_pnl"])}</div><div class="delta {"up" if data["total_pnl"]>=0 else "down"}">{fmt_pct(data["pnl_ratio"])}</div></div>
        <div class="kpi-card"><div class="label">收益率</div><div class="value" style="color:{"#ef4444" if data["pnl_ratio"]>=0 else "#16a34a"}">{fmt_pct(data["pnl_ratio"])}</div></div>
    </div>""", unsafe_allow_html=True)

    if data["stock_pnl"]:
        st.markdown("""<div style="font-size:16px;font-weight:600;color:#eef2ff;margin:16px 0 8px 0;">各股票盈亏</div>""", unsafe_allow_html=True)
        df = pd.DataFrame(data["stock_pnl"])
        fig = go.Figure(go.Bar(
            x=df["name"], y=df["pnl"],
            marker_color=[pnl_color(v) for v in df["pnl"]], text=[fmt_pnl(v) for v in df["pnl"]],
            textposition="outside", marker_line_width=0,
        ))
        fig.update_layout(
            margin=dict(t=8, b=0, l=20, r=20), height=260,
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=False, tickfont=dict(color="#666")),
            yaxis=dict(showgrid=False, tickfont=dict(color="#666"), zeroline=False),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

def page_portfolio():
    pf = get_user_portfolio(st.session_state.username)
    page_header("我的持仓", "持仓明细、市值与浮动盈亏", badge=st.session_state.username, ok=True)
    if pf.empty: st.info("暂无持仓"); return

    # 汇总行
    total_mv = pf["market_value"].sum()
    total_pnl = pf["pnl"].sum()
    total_shares = pf["shares"].sum()
    pnl_cls = "up" if total_pnl >= 0 else "down"
    st.markdown(f"""
    <div style="display:flex;gap:12px;margin-bottom:14px;flex-wrap:wrap;">
        <div style="background:rgba(10,20,42,.7);border:1px solid rgba(255,255,255,.04);border-radius:10px;padding:10px 18px;flex:1;min-width:100px;">
            <div style="font-size:11px;color:#64748b;">持仓市值</div>
            <div style="font-size:20px;font-weight:700;color:#eef2ff;white-space:nowrap;">{fmt_money_short(total_mv)}</div>
        </div>
        <div style="background:rgba(10,20,42,.7);border:1px solid rgba(255,255,255,.04);border-radius:10px;padding:10px 18px;flex:1;min-width:100px;">
            <div style="font-size:11px;color:#64748b;">总盈亏</div>
            <div style="font-size:20px;font-weight:700;color:{pnl_color(total_pnl)};white-space:nowrap;">{fmt_money_short(total_pnl)}</div>
        </div>
        <div style="background:rgba(10,20,42,.7);border:1px solid rgba(255,255,255,.04);border-radius:10px;padding:10px 18px;flex:1;min-width:100px;">
            <div style="font-size:11px;color:#64748b;">持股数</div>
            <div style="font-size:20px;font-weight:700;color:#eef2ff;">{fmt_num(total_shares)} 股</div>
        </div>
    </div>""", unsafe_allow_html=True)

    # 移动端：卡片
    st.markdown('<div class="mobile-only">', unsafe_allow_html=True)
    for _, r in pf.iterrows():
        pct = r["pnl_ratio"]; cls = pnl_class(pct)
        st.markdown(f"""
        <div class="stock-card">
            <div class="sc-header">
                <span class="sc-name">{esc(r["name"])} &nbsp;<span style="font-size:12px;color:#64748b">{esc(r["symbol"])}</span></span>
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
    render_table(d[["名称", "代码", "持仓", "成本", "现价", "市值", "盈亏", "收益率"]])
    st.markdown('</div></div>', unsafe_allow_html=True)

    # 买入时间（从最近一笔买入记录取）
    with get_db_cm() as conn:
        times = conn.execute("SELECT DISTINCT stock_symbol, MAX(trade_date) as t FROM transactions WHERE username=? AND trade_type='buy' GROUP BY stock_symbol ORDER BY t DESC", (st.session_state.username,)).fetchall()
    if times:
        st.caption(f"最近买入：{times[0]['stock_symbol']} {str(times[0]['t'])[:16]}" if times[0]['t'] else "")

def page_market_making():
    page_header("交易记录", "最近 100 笔交易流水与成交明细", badge=st.session_state.username, ok=True)
    with get_db_cm() as conn:
        rows = conn.execute("""
            SELECT t.stock_symbol, t.trade_type, t.price, t.shares, t.round, t.trade_date,
                   COALESCE(s.name, t.stock_symbol) AS nm
            FROM transactions t
            LEFT JOIN stocks s ON t.stock_symbol = s.symbol
            WHERE t.username=?
            ORDER BY t.trade_date DESC LIMIT 100
        """, (st.session_state.username,)).fetchall()
    if not rows:
        st.info("暂无交易记录"); return

    # 移动端：交易卡片
    st.markdown('<div class="mobile-only">', unsafe_allow_html=True)
    for r in rows[:50]:
        tp = "买入" if r["trade_type"]=="buy" else "卖出"
        cls = "up" if r["trade_type"]=="buy" else "down"
        amt = r["price"] * r["shares"]
        st.markdown(f"""
        <div class="stock-card" style="padding:10px 12px;">
            <div class="sc-header">
                <span class="sc-name" style="font-size:13px;">{esc(r['nm'])}</span>
                <span class="sc-pct {cls}" style="font-size:12px;">{tp}</span>
            </div>
            <div class="sc-detail" style="grid-template-columns:1fr 1fr 1fr;">
                <div>价格 <span class="val">¥{r['price']:.2f}</span></div>
                <div>数量 <span class="val">{r['shares']:,}股</span></div>
                <div>金额 <span class="val">¥{amt:,.0f}</span></div>
                <div>轮次 <span class="val">第{r['round']}轮</span></div>
                <div style="grid-column:span 2;">时间 <span class="val">{str(r['trade_date'])[:19] if r['trade_date'] else '-'}</span></div>
            </div>
        </div>""", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # 桌面端：表格
    st.markdown('<div class="desktop-only"><div class="desktop-table">', unsafe_allow_html=True)
    df = pd.DataFrame([{
        "股票": r["nm"], "类型": "买入" if r["trade_type"]=="buy" else "卖出",
        "价格": f"¥{r['price']:.2f}", "数量": f"{r['shares']:,}股",
        "金额": f"¥{r['price']*r['shares']:,.0f}", "轮次": f"第{r['round']}轮",
        "时间": str(r["trade_date"])[:19] if r["trade_date"] else "-",
    } for r in rows])
    render_table(df)
    st.markdown('</div></div>', unsafe_allow_html=True)

def page_trade_hall():
    stocks = get_stocks()
    if not stocks: st.error("无股票"); return
    mkt_open = is_market_open()
    mkt_round = get_market_round()
    page_header("交易大厅", f"第 {mkt_round} 轮 · {'可提交买卖委托' if mkt_open else '等待管理员开市'}", badge=("交易中" if mkt_open else "已闭市"), ok=mkt_open)

    if not mkt_open:
        st.markdown(f"""
        <div style="background:#0f1724;border:1px solid #1e2a3a;border-radius:6px;padding:20px;text-align:center;">
            <div style="color:#f23645;font-size:16px;font-weight:600;margin-bottom:6px;">市场已闭市</div>
            <div style="color:#5a6a7e;font-size:13px;">当前第 {mkt_round} 轮已结束，无法提交交易</div>
            <div style="color:#5a6a7e;font-size:12px;margin-top:4px;">请联系管理员开市后继续操作</div>
        </div>""", unsafe_allow_html=True)
        return

    opts = {f"{s['name']} ({s['symbol']})": s for s in stocks}

    # 桌面端：左右布局（交易+因子）
    st.markdown('<div class="desktop-only">', unsafe_allow_html=True)
    col_trade, col_factor = st.columns([1, 1])
    with col_trade:
        with st.form("trade_form_desk"):
            sel = st.selectbox("股票", list(opts.keys()))
            s = opts[sel]
            st.markdown(f"**当前价：{fmt_money(s['current_price'])}**")
            direction = st.radio("方向", ["买入", "卖出"], horizontal=True)
            price = st.number_input("价格", min_value=0.01, value=float(s["current_price"]), step=0.5, format="%.2f")
            shares = st.number_input("数量(股)", min_value=1, step=100, format="%d")
            if st.form_submit_button("确认交易", type="primary", use_container_width=True):
                tt = "buy" if direction == "买入" else "sell"
                with st.spinner("交易处理中..."):
                    ok, msg = add_trade(st.session_state.username, s["symbol"], tt, price, shares)
                if ok: st.success(msg)
                else: st.error(msg)
                st.rerun()
    with col_factor:
        factor_sym = st.selectbox("查看股票", list(opts.keys()), key="factor_sel")
        fs = opts[factor_sym]
        prev = fs["previous_close"] or fs["current_price"]
        pf_ = round(1 + 0.2 * (fs["premium_rate"] - 50) / 50, 4)
        cm_ = max(fs["industry_carbon_mean"], 1)
        cf_ = round(1 - 0.5 * (fs["carbon_price"] - cm_) / cm_, 4)
        st.markdown(f"""
        <div style="display:flex;gap:10px;flex-wrap:wrap;">
            <div style="flex:1;min-width:120px;background:rgba(10,20,42,.7);border:1px solid rgba(255,255,255,.04);border-radius:8px;padding:10px 14px;">
                <div style="font-size:11px;color:#94a3b8;">幸福因子</div>
                <div style="font-size:22px;font-weight:700;color:#{'16a34a' if pf_>=1 else 'ef4444'};">{pf_}</div>
                <div style="font-size:11px;color:#64748b;">溢价率 {fs['premium_rate']:.0f}%</div>
            </div>
            <div style="flex:1;min-width:120px;background:rgba(10,20,42,.7);border:1px solid rgba(255,255,255,.04);border-radius:8px;padding:10px 14px;">
                <div style="font-size:11px;color:#94a3b8;">碳因子</div>
                <div style="font-size:22px;font-weight:700;color:#{'16a34a' if cf_>=1 else 'ef4444'};">{cf_}</div>
                <div style="font-size:11px;color:#64748b;">碳价 {fs['carbon_price']:.0f}（均值{cm_:.0f}）</div>
            </div>
            <div style="flex:1;min-width:120px;background:rgba(10,20,42,.7);border:1px solid rgba(255,255,255,.04);border-radius:8px;padding:10px 14px;">
                <div style="font-size:11px;color:#94a3b8;">上轮收盘</div>
                <div style="font-size:22px;font-weight:700;">{fmt_money(prev)}</div>
                <div style="font-size:11px;color:#64748b;">理论价 {fmt_money(round(prev*max(1,pf_)*cf_,2))}</div>
            </div>
        </div>""", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # 挂单状态
    with get_db_cm() as conn:
        pending = conn.execute("""
            SELECT stock_symbol, trade_type, SUM(shares) as s FROM order_book
            WHERE username=?
            GROUP BY stock_symbol, trade_type
        """, (st.session_state.username,)).fetchall()
    if pending:
        pend_parts = [f"{'买入' if p['trade_type']=='buy' else '卖出'} {p['s']}股 {p['stock_symbol']}" for p in pending]
        st.info(f"⏳ 当前挂单：{'，'.join(pend_parts)}")

    # 移动端交易面板
    st.markdown('<div class="mobile-only" style="margin-bottom:12px;">', unsafe_allow_html=True)
    st.markdown("""<div style="font-size:15px;font-weight:600;color:#eef2ff;margin-bottom:8px;">交易</div>""", unsafe_allow_html=True)
    with st.form("trade_form_mobile"):
        cols = st.columns([2, 1, 1, 1])
        with cols[0]:
            m_sel = st.selectbox("股票", list(opts.keys()), label_visibility="collapsed")
        with cols[1]:
            m_dir = st.radio("方向", ["买入", "卖出"], horizontal=True, label_visibility="collapsed")
        m_s = opts[m_sel]
        with cols[2]:
            m_price = st.number_input("价格", min_value=0.01, value=float(m_s["current_price"]), step=0.5, format="%.2f", label_visibility="collapsed")
        with cols[3]:
            m_shares = st.number_input("数量", min_value=1, step=100, format="%d", label_visibility="collapsed")
        if st.form_submit_button("交易", type="primary", use_container_width=True):
            m_tt = "buy" if m_dir == "买入" else "sell"
            with st.spinner("交易处理中..."):
                ok, msg = add_trade(st.session_state.username, m_s["symbol"], m_tt, m_price, m_shares)
            if ok: st.success(msg)
            else: st.error(msg)
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

def build_professional_kline_view(df_src, symbol, target=72):
    """Build a dense display-only K-line series from sparse game rounds."""
    if df_src is None or df_src.empty:
        return pd.DataFrame()
    src = df_src.sort_values("round").reset_index(drop=True).copy()
    if len(src) >= 30:
        out = src.copy()
        out["display_round"] = np.arange(1, len(out) + 1)
        out["source_round"] = out["round"]
        out["is_anchor"] = True
    else:
        seed = sum((i + 1) * ord(ch) for i, ch in enumerate(str(symbol))) % (2**32)
        rng = np.random.default_rng(seed)
        target = max(target, len(src) * 12, 48)
        idx = np.arange(target)
        anchor_x = np.linspace(0, target - 1, len(src))
        anchor_close = src["close_price"].astype(float).to_numpy()
        if len(src) == 1:
            anchor_close = np.array([float(src.iloc[0]["open_price"]), float(src.iloc[0]["close_price"])])
            anchor_x = np.array([0, target - 1])
        base_close = np.interp(idx, anchor_x, anchor_close)
        price_scale = max(float(np.nanmean(base_close)), 1.0)
        wave = np.sin(np.linspace(0, 4.8 * np.pi, target) + rng.uniform(0, np.pi)) * price_scale * 0.018
        wave += np.sin(np.linspace(0, 1.8 * np.pi, target) + rng.uniform(0, np.pi)) * price_scale * 0.028
        noise = rng.normal(0, price_scale * 0.007, target).cumsum() * 0.12
        close = np.maximum(base_close + wave + noise, price_scale * 0.03)
        close[0] = float(src.iloc[0]["open_price"])
        close[-1] = float(src.iloc[-1]["close_price"])
        open_ = np.r_[float(src.iloc[0]["open_price"]), close[:-1] + rng.normal(0, price_scale * 0.006, target - 1)]
        body_max = price_scale * 0.035
        close = open_ + np.clip(close - open_, -body_max, body_max)
        close[-1] = float(src.iloc[-1]["close_price"])
        real_high = float(src["high_price"].max())
        real_low = max(float(src["low_price"].min()), 0.01)
        open_ = np.clip(open_, real_low, real_high)
        close = np.clip(close, real_low, real_high)
        high = np.maximum(open_, close) + rng.uniform(price_scale * 0.004, price_scale * 0.022, target)
        low = np.minimum(open_, close) - rng.uniform(price_scale * 0.004, price_scale * 0.022, target)
        high = np.minimum(high, real_high)
        low = np.maximum(low, real_low)
        real_vol = max(float(src["volume"].mean()), 1.0)
        vol_wave = 0.75 + 0.35 * np.sin(np.linspace(0, 3.2 * np.pi, target) + 1.1)
        volume = np.maximum(real_vol * vol_wave * rng.uniform(0.72, 1.28, target), 1).astype(int)
        out = pd.DataFrame({
            "round": np.arange(1, target + 1),
            "display_round": np.arange(1, target + 1),
            "source_round": np.nan,
            "is_anchor": False,
            "open_price": open_,
            "high_price": high,
            "low_price": low,
            "close_price": close,
            "volume": volume,
        })
        anchor_idx = np.rint(np.linspace(0, target - 1, len(src))).astype(int)
        for idx_real, (_, real_row) in zip(anchor_idx, src.iterrows()):
            out.loc[idx_real, "open_price"] = float(real_row["open_price"])
            out.loc[idx_real, "high_price"] = float(real_row["high_price"])
            out.loc[idx_real, "low_price"] = float(real_row["low_price"])
            out.loc[idx_real, "close_price"] = float(real_row["close_price"])
            out.loc[idx_real, "volume"] = float(row_get(real_row, "volume", out.loc[idx_real, "volume"]) or 0)
            out.loc[idx_real, "source_round"] = int(real_row["round"])
            out.loc[idx_real, "is_anchor"] = True
        prev = out["close_price"].shift(1).fillna(out["open_price"])
        out["change_pct"] = ((out["close_price"] - prev) / prev.replace(0, np.nan) * 100).fillna(0)
        for idx_real, (_, real_row) in zip(anchor_idx, src.iterrows()):
            out.loc[idx_real, "change_pct"] = float(row_get(real_row, "change_pct", out.loc[idx_real, "change_pct"]) or 0)
    out["mid"] = out["close_price"].rolling(20, min_periods=5).mean()
    std = out["close_price"].rolling(20, min_periods=5).std().fillna(0)
    out["upper"] = out["mid"] + 2 * std
    out["lower"] = out["mid"] - 2 * std
    out["ma5"] = out["close_price"].rolling(5, min_periods=2).mean()
    out["ma10"] = out["close_price"].rolling(10, min_periods=3).mean()
    return out

def kline_display_range(df_k, ref_price=None):
    """Use a robust display range so one bad price does not flatten the chart."""
    vals = pd.concat([
        df_k["open_price"], df_k["high_price"], df_k["low_price"], df_k["close_price"],
        df_k.get("upper", pd.Series(dtype=float)),
        df_k.get("lower", pd.Series(dtype=float)),
    ], ignore_index=True).astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    vals = vals[vals > 0]
    if vals.empty:
        return [0, 1], 0, 1, False
    raw_min, raw_max = float(vals.min()), float(vals.max())
    q_low = float(vals.quantile(0.03))
    q_high = float(vals.quantile(0.97))
    median = float(vals.median())
    if ref_price and ref_price > 0:
        lo = max(min(q_low, ref_price * 0.82), ref_price * 0.55, 0.01)
        hi = min(max(q_high, ref_price * 1.18), ref_price * 1.80)
    else:
        lo, hi = q_low, q_high
    if hi <= lo:
        lo, hi = median * 0.92, median * 1.08
    pad = max((hi - lo) * 0.08, median * 0.015, 0.05)
    display_range = [max(lo - pad, 0.01), hi + pad]
    clipped = raw_min < display_range[0] or raw_max > display_range[1]
    return display_range, raw_min, raw_max, clipped

def page_kline():
    stocks = get_stocks()
    if not stocks: st.info("无数据"); return
    page_header("K 线展板", "轮次走势、成交量与均线观察", badge=st.session_state.username, ok=True)
    opts = {f"{s['name']} ({s['symbol']})": s for s in stocks}
    sel = st.selectbox("选择股票", list(opts.keys()))
    sym = opts[sel]["symbol"]
    data = get_kline_data(sym)

    if not data:
        st.info("暂无行情K线数据")
        return

    # ── OHLC 校验 + 数据清洗 ──
    cleaned = []
    dirty = 0
    for d in data:
        o = row_get(d, "open_price", 0)
        h = row_get(d, "high_price", 0)
        l = row_get(d, "low_price", 0)
        c = row_get(d, "close_price", 0)
        if any(v is None or v < 0 for v in [o,h,l,c]) or h < max(o,c) or l > min(o,c):
            dirty += 1; continue
        d["round"] = row_get(d, "round", len(cleaned)+1)
        cleaned.append(d)
    if dirty:
        st.warning(f"已过滤 {dirty} 条异常数据")
    if not cleaned:
        st.info("暂无合规K线数据"); return

    # 轮次筛选
    max_round = max(d["round"] for d in cleaned)
    round_options = ["全部"] + [f"第{r}轮" for r in range(1, max_round + 1)]
    round_sel = st.selectbox("筛选轮次", round_options, key="kline_round")
    if round_sel != "全部":
        target_r = int(round_sel.replace("第","").replace("轮",""))
        cleaned = [d for d in cleaned if d["round"] == target_r]
        if not cleaned: st.info("该轮次无数据"); return

    df_k = pd.DataFrame(cleaned)
    # X轴用轮次序号（时间升序）
    df_k = df_k.sort_values("round").reset_index(drop=True)
    df_k["x_label"] = df_k["round"].apply(lambda r: f"第{r}轮")
    df_k["x_pos"] = np.arange(len(df_k)) + 1
    x_values = df_k["x_pos"]

    latest = df_k.iloc[-1]
    first_open = float(df_k.iloc[0]["open_price"])
    latest_close = float(latest["close_price"])
    total_change = round((latest_close - first_open) / first_open * 100, 2) if first_open else 0
    latest_change = float(latest.get("change_pct", 0) or 0)
    total_volume = int(df_k["volume"].sum())
    high_price = float(df_k["high_price"].max())
    low_price = float(df_k["low_price"].min())
    latest_open = float(latest["open_price"])
    latest_high = float(latest["high_price"])
    latest_low = float(latest["low_price"])
    latest_volume = int(latest["volume"])
    latest_round = int(latest["round"])
    latest_cls = "up" if latest_change >= 0 else "down"
    total_cls = "up" if total_change >= 0 else "down"
    st.markdown(f"""
    <div class="chart-summary">
        <div class="chart-metric"><div class="label">最新价 · 第{latest_round}轮</div><div class="value {latest_cls}">{fmt_money(latest_close)}</div></div>
        <div class="chart-metric"><div class="label">本轮涨跌</div><div class="value {latest_cls}">{latest_change:+.2f}%</div></div>
        <div class="chart-metric"><div class="label">区间涨跌</div><div class="value {total_cls}">{total_change:+.2f}%</div></div>
        <div class="chart-metric"><div class="label">开 / 高 / 低</div><div class="value">{latest_open:,.2f} / {latest_high:,.2f} / {latest_low:,.2f}</div></div>
        <div class="chart-metric"><div class="label">本轮量 / 区间量</div><div class="value">{fmt_num(latest_volume)} / {fmt_num(total_volume)}</div></div>
    </div>
    """, unsafe_allow_html=True)

    df_k = build_professional_kline_view(df_k, sym)
    df_k["x_label"] = df_k["display_round"].apply(lambda r: f"{int(r)}")
    df_k["x_pos"] = df_k["display_round"]
    x_values = df_k["x_pos"]
    high_price = float(df_k["high_price"].max())
    low_price = float(df_k["low_price"].min())
    latest_close = float(df_k.iloc[-1]["close_price"])
    latest_mid = float(df_k["mid"].dropna().iloc[-1]) if df_k["mid"].notna().any() else latest_close
    latest_upper = float(df_k["upper"].dropna().iloc[-1]) if df_k["upper"].notna().any() else high_price
    latest_lower = float(df_k["lower"].dropna().iloc[-1]) if df_k["lower"].notna().any() else low_price
    st.markdown(f"""
    <div class="boll-strip">
        <b>BOLL</b><span>[20,2]</span>
        <span style="color:#d6a11d;">MID:{latest_mid:,.2f}</span>
        <span style="color:#4c8fbd;">UPPER:{latest_upper:,.2f}</span>
        <span style="color:#d957a8;">LOWER:{latest_lower:,.2f}</span>
    </div>
    """, unsafe_allow_html=True)

    # ── A股标准色：红涨绿跌（实体填充） ──
    RED_UP   = "#d64b45"   # 阳线红
    GREEN_DN = "#07984f"   # 阴线绿
    up_mask  = df_k["close_price"] >= df_k["open_price"]
    body_fill = ["rgba(255,255,255,0)" if u else GREEN_DN for u in up_mask]
    candle_line = [RED_UP if u else GREEN_DN for u in up_mask]
    vol_fill = ["rgba(255,255,255,0)" if u else GREEN_DN for u in up_mask]
    vol_line = [RED_UP if u else GREEN_DN for u in up_mask]

    # ── 主图蜡烛 + 成交量副图 ──
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.03, row_heights=[0.75, 0.25])

    for is_up, color in [(True, RED_UP), (False, GREEN_DN)]:
        wick_x, wick_y = [], []
        for _, r in df_k[up_mask == is_up].iterrows():
            wick_x.extend([r["x_pos"], r["x_pos"], None])
            wick_y.extend([r["low_price"], r["high_price"], None])
        fig.add_trace(go.Scatter(
            x=wick_x, y=wick_y, mode="lines",
            line=dict(color=color, width=1.05),
            hoverinfo="skip", showlegend=False,
        ), row=1, col=1)

    body_base = df_k[["open_price", "close_price"]].min(axis=1)
    body_height = (df_k["close_price"] - df_k["open_price"]).abs()
    min_body = max((high_price - low_price) * 0.008, latest_close * 0.001, 0.01)
    body_height = body_height.where(body_height > min_body, min_body)
    fig.add_trace(go.Bar(
        x=x_values, y=body_height, base=body_base, width=0.42,
        marker=dict(color=body_fill, line=dict(color=candle_line, width=1.15)),
        name="K线", showlegend=False,
        customdata=np.stack([df_k["display_round"], df_k["source_round"].fillna(0), df_k["open_price"], df_k["high_price"], df_k["low_price"], df_k["close_price"], df_k["change_pct"], df_k["volume"]], axis=-1),
        hovertemplate=(
            "轮次 %{customdata[0]}<br>数据轮次 %{customdata[1]:.0f}<br>"
            "开盘 %{customdata[2]:,.2f}<br>最高 %{customdata[3]:,.2f}<br>"
            "最低 %{customdata[4]:,.2f}<br>收盘 %{customdata[5]:,.2f}<br>"
            "涨跌 %{customdata[6]:+.2f}%<br>成交量 %{customdata[7]:,.0f}<extra></extra>"
        ),
    ), row=1, col=1)

    fig.add_trace(go.Bar(
        x=x_values, y=df_k["volume"], width=0.42,
        marker=dict(color=vol_fill, line=dict(color=vol_line, width=1.05)),
        name="成交量", showlegend=False,
        customdata=df_k["round"],
        hovertemplate="第%{customdata}轮<br>成交量 %{y:,.0f}<extra></extra>",
    ), row=2, col=1)
    for period, color, name in [(5, "#d6a11d", "VOL5"), (10, "#4c8fbd", "VOL10")]:
        vol_ma = df_k["volume"].rolling(period, min_periods=2).mean()
        fig.add_trace(go.Scatter(
            x=x_values, y=vol_ma, mode="lines",
            line=dict(color=color, width=1.0), showlegend=False,
            hovertemplate=f"{name} %{{y:,.0f}}<extra></extra>",
        ), row=2, col=1)

    # ── BOLL / MA 线 ──
    for col, color, name, width in [
        ("upper", "#6b9ec7", "UPPER", 1.25),
        ("mid", "#d6a11d", "MID", 1.25),
        ("lower", "#d957a8", "LOWER", 1.25),
        ("ma5", "#f59e0b", "MA5", 1.0),
        ("ma10", "#4c8fbd", "MA10", 1.0),
    ]:
        if col in df_k and df_k[col].notna().any():
            fig.add_trace(go.Scatter(x=x_values, y=df_k[col], mode="lines",
                line=dict(color=color, width=width), name=name,
                hovertemplate=f"{name} %{{y:,.2f}}<extra></extra>"), row=1, col=1)

    high_idx = int(df_k["high_price"].idxmax())
    low_idx = int(df_k["low_price"].idxmin())
    fig.add_annotation(x=df_k.loc[high_idx, "x_pos"], y=high_price, text=f"高 {high_price:,.2f}",
                       showarrow=True, arrowhead=2, arrowsize=0.8, arrowwidth=1,
                       arrowcolor="#94a3b8", font=dict(size=13, color="#e2e8f0"),
                       bgcolor="rgba(15,23,36,.82)", bordercolor="#1e2a3a", row=1, col=1)
    fig.add_annotation(x=df_k.loc[low_idx, "x_pos"], y=low_price, text=f"低 {low_price:,.2f}",
                       showarrow=True, arrowhead=2, arrowsize=0.8, arrowwidth=1,
                       arrowcolor="#94a3b8", font=dict(size=13, color="#e2e8f0"),
                       bgcolor="rgba(15,23,36,.82)", bordercolor="#1e2a3a", row=1, col=1)

    # ── 布局：同花顺/东方财富专业风格 ──
    fig.update_layout(
        height=600,
        margin=dict(t=24, b=8, l=56, r=56),
        plot_bgcolor="#0b1220", paper_bgcolor="#0b1220",
        xaxis_rangeslider_visible=False,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    bgcolor="rgba(15,23,36,0.86)", bordercolor="#1e2a3a", borderwidth=0.5,
                    font=dict(color="#cbd5e1")),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#111827", font_size=11, font_color="#e5e7eb",
                        bordercolor="#334155"),
        font=dict(family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif",
                  size=11, color="#94a3b8"),
        # 十字光标（同花顺风格：实线细十字）
        xaxis=dict(type="linear",
                   showspikes=True, spikemode="across", spikethickness=0.8,
                   spikecolor="#64748b", spikedash="solid"),
        yaxis=dict(showspikes=True, spikethickness=0.8,
                   spikecolor="#64748b", spikedash="solid"),
        bargap=0.42,
        dragmode="zoom",
    )
    fig.add_hline(y=latest_close, line_width=1, line_dash="dot", line_color="#fbbf24",
                  annotation_text=f"最新 {latest_close:,.2f}", annotation_position="top right",
                  annotation_font_color="#fbbf24", row=1, col=1)
    fig.add_hline(y=first_open, line_width=7, line_dash="solid", line_color="rgba(148,163,184,.22)",
                  row=1, col=1)

    # 主图 Y 轴：右侧价格标签 + 左侧涨跌幅参考轴
    y_range, _, _, _ = kline_display_range(df_k, first_open)
    pct_ticks = np.linspace(y_range[0], y_range[1], 6)
    pct_text = [f"{((v - first_open) / first_open * 100):+.2f}%" if first_open else "0.00%" for v in pct_ticks]
    fig.update_yaxes(
        range=y_range,
        showgrid=True, gridcolor="#1e2a3a", gridwidth=1, griddash="dot",
        tickmode="array", tickvals=pct_ticks, ticktext=[fmt_axis_num(v) for v in pct_ticks],
        tickfont=dict(size=12, color="#94a3b8", family="monospace"),
        side="right", row=1, col=1,
        zeroline=False,
        title_text="", title_font=dict(size=10, color="#94a3b8"),
    )
    fig.update_layout(
        yaxis3=dict(
            overlaying="y", anchor="x", side="left", range=y_range,
            tickmode="array", tickvals=pct_ticks, ticktext=pct_text,
            showgrid=False, zeroline=False, ticks="outside",
            tickfont=dict(size=12, color="#94a3b8", family="monospace"),
            title=dict(text="", font=dict(size=10, color="#94a3b8")),
        )
    )
    tick_step = max(1, len(df_k)//6)
    tick_vals = x_values.iloc[::tick_step]
    tick_text = df_k["x_label"].iloc[::tick_step]
    fig.update_xaxes(
        showgrid=False, type="linear",
        tickmode="array", tickvals=tick_vals, ticktext=tick_text,
        tickfont=dict(size=11, color="#94a3b8"),
        row=1, col=1,
    )

    # 成交量副图
    fig.update_yaxes(
        showgrid=True, gridcolor="#1e2a3a", gridwidth=1, griddash="dot",
        tickmode="array",
        tickvals=np.linspace(0, float(df_k["volume"].max() or 1), 4),
        ticktext=[fmt_axis_num(v) for v in np.linspace(0, float(df_k["volume"].max() or 1), 4)],
        tickfont=dict(size=10, color="#94a3b8"), side="right", row=2, col=1,
        zeroline=False,
    )
    fig.update_xaxes(
        showgrid=False, type="linear",
        tickmode="array",
        tickvals=tick_vals,
        ticktext=tick_text,
        tickfont=dict(size=11, color="#94a3b8"),
        row=2, col=1,
    )

    # 工具栏：极简，只保留基础功能
    config = {
        "displayModeBar": False,
        "modeBarButtonsToRemove": ["lasso2d", "select2d", "sendDataToCloud",
                                     "autoScale2d", "toggleSpikelines",
                                     "zoomIn2d", "zoomOut2d"],
        "modeBarButtonsToAdd": [],
        "displaylogo": False,
        "scrollZoom": True,
        "responsive": True,
    }
    st.markdown('<div class="chart-panel pro-chart">', unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True, config=config)
    st.markdown('</div>', unsafe_allow_html=True)

    # 数据明细表
    st.divider()
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#eef2ff;margin-bottom:8px">每轮数据明细</div>""", unsafe_allow_html=True)
    disp = pd.DataFrame(cleaned).tail(30).copy()
    disp["开盘"] = disp["open_price"].apply(lambda x: f"¥{x:,.2f}")
    disp["最高"] = disp["high_price"].apply(lambda x: f"¥{x:,.2f}")
    disp["最低"] = disp["low_price"].apply(lambda x: f"¥{x:,.2f}")
    disp["收盘"] = disp["close_price"].apply(lambda x: f"¥{x:,.2f}")
    disp["涨跌幅"] = disp["change_pct"].apply(lambda x: f"{x:+.2f}%")
    disp["成交量"] = disp["volume"].apply(lambda x: f"{x:,.0f}")
    render_table(disp[["round","开盘","最高","最低","收盘","涨跌幅","成交量"]].rename(columns={"round":"轮次"}))

def page_admin_stock_summary():
    page_header("股票汇总", "全市场持仓、市值与盈亏概览", badge="管理员", ok=True)
    stats = get_platform_stats()
    summary = get_admin_summary()
    if summary.empty: st.info("无数据"); return
    held_stocks = int((summary["总持仓量"] > 0).sum())
    st.markdown(f"""<div class="kpi-grid">{kpi_card("总市值", fmt_money(stats["total_mv"]))}{kpi_card("总盈亏", fmt_money(stats["total_pnl"]), fmt_pct(0) if stats["total_pnl"]==0 else None, stats["total_pnl"]>=0)}{kpi_card("活跃用户", fmt_num(stats["active_users"]))}{kpi_card("持仓股票", f"{held_stocks}/{len(summary)}")}</div>""", unsafe_allow_html=True)
    render_admin_risk_panel()
    sdf = summary.sort_values("总盈亏")
    fig = go.Figure(go.Bar(x=sdf["股票名称"], y=sdf["总盈亏"], marker_color=[pnl_color(v) for v in sdf["总盈亏"]], text=[fmt_money(v) for v in sdf["总盈亏"]], textposition="outside"))
    fig.update_layout(height=280, margin=dict(t=8, b=0, l=0, r=0), plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)"); fig.update_xaxes(showgrid=False); fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.markdown('<div class="section-title">个股行情检测</div>', unsafe_allow_html=True)
    for _, row in summary.iterrows():
        pnl = float(row["总盈亏"])
        ratio = float(row["收益率"])
        pill_cls = "up" if pnl >= 0 else "down"
        val_cls = "pos" if pnl > 0 else "neg" if pnl < 0 else ""
        st.markdown(f"""
        <div class="stock-audit-card">
            <div class="stock-audit-head">
                <div class="stock-audit-title">{esc(row['股票名称'])}<span class="stock-audit-code">{esc(row['代码'])}</span></div>
                <div class="stock-audit-pill {pill_cls}">{esc(fmt_pct(ratio))}</div>
            </div>
            <div class="stock-audit-metrics">
                <div class="stock-audit-metric"><div class="label">当前价</div><div class="value">{esc(fmt_money(row['当前价']))}</div></div>
                <div class="stock-audit-metric"><div class="label">持有人</div><div class="value">{esc(fmt_num(row['持有用户数']))}</div></div>
                <div class="stock-audit-metric"><div class="label">总持仓</div><div class="value">{esc(fmt_num(row['总持仓量']))}</div></div>
                <div class="stock-audit-metric"><div class="label">总成本</div><div class="value">{esc(fmt_money(row['总成本']))}</div></div>
                <div class="stock-audit-metric"><div class="label">总市值</div><div class="value">{esc(fmt_money(row['总市值']))}</div></div>
                <div class="stock-audit-metric"><div class="label">总盈亏</div><div class="value {val_cls}">{esc(fmt_money(pnl))}</div></div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        with st.expander(f"查看 {row['股票名称']} ({row['代码']}) 持有人明细"):
            d = get_holder_detail(row["代码"])
            if not d.empty:
                dd = d.copy()
                dd["持仓量"] = dd["持仓量"].apply(lambda x: fmt_num(x))
                dd["成本价"] = dd["成本价"].apply(lambda x: f"¥{x:,.2f}")
                dd["当前价"] = dd["当前价"].apply(lambda x: f"¥{x:,.2f}")
                dd["盈亏"] = dd["盈亏"].apply(lambda x: f"¥{x:,.2f}")
                dd["收益率"] = dd["收益率"].apply(lambda x: f"{x:,.2f}%")
                render_table(dd, compact=True)
            else:
                st.info("暂无持仓明细")
    disp = summary.copy()
    disp["当前价"] = disp["当前价"].apply(lambda x: f"¥{x:,.2f}")
    disp["持有用户数"] = disp["持有用户数"].apply(lambda x: fmt_num(x))
    disp["总持仓量"] = disp["总持仓量"].apply(lambda x: fmt_num(x))
    disp["总成本"] = disp["总成本"].apply(lambda x: f"¥{x:,.2f}")
    disp["总市值"] = disp["总市值"].apply(lambda x: f"¥{x:,.2f}")
    disp["总盈亏"] = disp["总盈亏"].apply(lambda x: f"¥{x:,.2f}")
    disp["收益率"] = disp["收益率"].apply(lambda x: f"{x:,.2f}%")
    st.markdown('<div class="desktop-table">', unsafe_allow_html=True)
    render_table(disp)
    st.markdown('</div>', unsafe_allow_html=True)

def page_admin_stock_mgmt():
    page_header("股票管理", "维护股票基础参数、定价因子与批量导入", badge="管理员", ok=True)
    if st.session_state.get("stock_add_ok"): st.success(st.session_state.stock_add_ok); st.session_state.stock_add_ok = ""
    if st.session_state.get("stock_add_err"): st.error(st.session_state.stock_add_err); st.session_state.stock_add_err = ""
    with st.expander("添加新股票（Excel基础信息表）"):
        with st.form("add_stock_form"):
            c1, c2, c3 = st.columns(3)
            with c1: sym = st.text_input("股票代码", max_chars=10, key="asym")
            with c2: name = st.text_input("公司名称", key="aname")
            with c3: ts_ = st.number_input("总股本（万股）", min_value=1.0, value=10000.0, step=1000.0, format="%.0f", key="ats")
            c4, c5 = st.columns(2)
            with c4: rev = st.number_input("初始净利润（万）", min_value=1.0, value=100.0, step=10.0, format="%.0f", key="arev")
            with c5: ipe = st.number_input("行业PE", min_value=1.0, value=20.0, step=1.0, format="%.1f", key="aipe")
            preview_price = calc_initial_price(rev, ts_, ipe, sym.strip().upper())
            st.caption(f"📌 公式初始价 = 初始净利润×10000÷总股本÷行业PE = **¥{preview_price}**")
            warn = initial_price_warning(preview_price)
            if warn: st.warning(warn)
            if st.form_submit_button("添加", type="primary", use_container_width=True):
                s, n = sym.strip().upper(), name.strip()
                if s and n and ts_ > 0 and rev > 0 and ipe > 0:
                    ok, msg = add_stock(s, n, ts_, rev, ipe)
                    if ok:
                        log_action(st.session_state.username, "stock_add", s, f"name={n}, ts={ts_}, rev={rev}, pe={ipe}")
                        st.session_state.stock_add_ok = msg
                    else: st.session_state.stock_add_err = msg
                else: st.session_state.stock_add_err = "请完整填写所有字段"
                st.rerun()
    with st.expander("批量添加股票（上传 Excel）"):
        st.caption("Excel 五列：股票代码 / 公司名称 / 总股本(万股) / 初始净利润(万) / 行业PE；初始价按公式计算")
        uploaded_stocks = st.file_uploader("选择 Excel 文件", type=["xlsx"], key="batch_stocks")
        if uploaded_stocks:
            df = pd.read_excel(uploaded_stocks)
            if df.shape[1] >= 5:
                valid = []
                for i in range(len(df)):
                    if pd.notna(df.iloc[i, 0]) and pd.notna(df.iloc[i, 1]):
                        try:
                            sym = str(df.iloc[i, 0]).strip().upper()
                            name = str(df.iloc[i, 1]).strip()
                            ts = float(df.iloc[i, 2])
                            rev = float(df.iloc[i, 3])
                            pe = float(df.iloc[i, 4])
                            if sym and name and ts > 0 and rev > 0 and pe > 0:
                                valid.append((sym, name, ts, rev, pe))
                        except: pass
                st.info(f"检测到 {len(valid)} 只股票")
                if st.button("一键批量添加", type="primary"):
                    created, skipped = 0, []
                    for sym, name, ts, rev, pe in valid:
                        ok, msg = add_stock(sym, name, ts, rev, pe)
                        if ok: created += 1
                        else: skipped.append(f"{sym}({msg})")
                    result = f"成功添加 {created} 只股票"
                    if skipped: result += f"，跳过 {len(skipped)} 只: {', '.join(skipped[:5])}"
                    st.success(result); st.rerun()
            else:
                st.error("Excel 格式错误，至少需要五列（代码/名称/总股本/净利润/行业PE）")
    stocks = get_stocks()
    if not stocks: st.info("无"); return
    sdf = pd.DataFrame(stocks)
    sdf["price"] = sdf["current_price"].apply(lambda x: f"¥{x:,.2f}")
    sdf["总股本"] = sdf.get("total_shares", 10000).apply(lambda x: f"{x:,.0f}")
    sdf["净利润"] = sdf.get("revenue", 100000).apply(lambda x: f"{x:,.0f}")
    sdf["行业PE"] = sdf.get("industry_pe", 20).apply(lambda x: f"{x:.1f}")
    sdf["碳排"] = sdf.get("carbon_price", 50).apply(lambda x: f"{x:.1f}")
    sdf["碳排均值"] = sdf.get("industry_carbon_mean", 50).apply(lambda x: f"{x:.1f}")
    sdf["幸福度"] = sdf.get("premium_rate", 50).apply(lambda x: f"{x:.0f}%")
    sdf["lu"] = sdf["last_update"].apply(lambda x: str(x)[:19] if x else "-")
    st.markdown('<div class="desktop-table">', unsafe_allow_html=True)
    render_table(sdf[["symbol", "name", "总股本", "净利润", "行业PE", "price", "碳排", "碳排均值", "幸福度", "lu"]].rename(
        columns={"symbol": "代码", "name": "名称", "price": "当前价", "lu": "更新"}))
    st.markdown('</div>', unsafe_allow_html=True)

    # 因子可视化
    st.divider()
    st.markdown("""<div style="font-size:16px;font-weight:600;color:#eef2ff;margin-bottom:12px">定价因子面板</div>""", unsafe_allow_html=True)
    fsel = st.selectbox("查看股票", [f"{s['name']} ({s['symbol']})" for s in stocks], key="admin_factor")
    fsym = fsel.split("(")[1].rstrip(")")
    fs = next(x for x in stocks if x["symbol"] == fsym)
    prev = fs["previous_close"] or fs["current_price"]
    pf = round(1 + 0.2 * (fs["premium_rate"] - 50) / 50, 4)
    cm = max(fs["industry_carbon_mean"], 1)
    cf = round(1 - 0.5 * (fs["carbon_price"] - cm) / cm, 4)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"""
        <div style="background:rgba(10,20,42,.7);border:1px solid rgba(255,255,255,.04);border-radius:10px;padding:16px 20px;">
            <div style="font-size:13px;color:#94a3b8;margin-bottom:8px;">幸福度（溢价率）</div>
            <div style="display:flex;align-items:center;gap:12px;">
                <div style="flex:1;background:#e8ecf1;border-radius:6px;height:8px;overflow:hidden;">
                    <div style="width:{fs['premium_rate']}%;height:100%;background:#{'16a34a' if pf>=1 else 'ef4444'};border-radius:6px;"></div>
                </div>
                <span style="font-size:28px;font-weight:600;color:#{'16a34a' if pf>=1 else 'ef4444'};">{pf}</span>
            </div>
            <div style="font-size:12px;color:#64748b;margin-top:4px;">溢价率 {fs['premium_rate']:.0f}% | 上轮收盘 {fmt_money(prev)} | 理论价 {fmt_money(round(prev*pf*cf,2))}</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div style="background:rgba(10,20,42,.7);border:1px solid rgba(255,255,255,.04);border-radius:10px;padding:16px 20px;">
            <div style="font-size:13px;color:#94a3b8;margin-bottom:8px;">碳排放（碳价）</div>
            <div style="display:flex;align-items:center;gap:12px;">
                <div style="flex:1;background:#e8ecf1;border-radius:6px;height:8px;overflow:hidden;">
                    <div style="width:{max(0,min(100,(1-cf)*200+50)):.0f}%;height:100%;background:#{'16a34a' if cf>=1 else 'ef4444'};border-radius:6px;"></div>
                </div>
                <span style="font-size:28px;font-weight:600;color:#{'16a34a' if cf>=1 else 'ef4444'};">{cf}</span>
            </div>
            <div style="font-size:12px;color:#64748b;margin-top:4px;">当前碳价 {fs['carbon_price']:.0f} | 行业均值 {cm:.0f} | 碳价越低价格越涨</div>
        </div>""", unsafe_allow_html=True)

    st.divider()
    st.markdown("""<div style="font-size:16px;font-weight:600;color:#eef2ff;margin-bottom:12px">股票信息（Excel基础信息表）</div>""", unsafe_allow_html=True)
    st.caption("价格由撮合逻辑自动生成，不可手动修改。重开赛局时会按基础参数公式恢复初始价。")
    for s in stocks:
        with st.expander(f"{s['name']} ({s['symbol']}) — 当前价 {fmt_money(s['current_price'])}"):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown("**基础参数**")
                col_a, col_b = st.columns(2)
                with col_a:
                    ts_ = st.number_input("总股本（万股）", min_value=1.0, value=float(row_get(s,"total_shares",10000)), step=1000.0, format="%.0f", key=f"ts_{s['id']}")
                    rev = st.number_input("初始净利润（万）", min_value=1.0, value=float(row_get(s,"revenue",100000)), step=1000.0, format="%.0f", key=f"rev_{s['id']}")
                    ipe = st.number_input("行业PE", min_value=1.0, value=float(row_get(s,"industry_pe",20)), step=1.0, format="%.1f", key=f"ipe_{s['id']}")
                with col_b:
                    cp = st.number_input("当前碳排", min_value=0.0, max_value=200.0, value=float(s["carbon_price"]), step=1.0, format="%.1f", key=f"cp_{s['id']}")
                    icm = st.number_input("行业碳排均值", min_value=1.0, max_value=200.0, value=float(s["industry_carbon_mean"]), step=1.0, format="%.1f", key=f"icm_{s['id']}")
                    pr = st.number_input("当前幸福度（%）", min_value=0.0, max_value=100.0, value=float(s["premium_rate"]), step=1.0, format="%.0f", key=f"pr_{s['id']}")
                init_p = calc_initial_price(rev, ts_, ipe, s["symbol"])
                st.info(f"📐 公式初始价 = {rev:,.0f}×10000÷{ts_:,.0f}÷{ipe:,.1f} = **¥{init_p}** ｜ 当前市价 **¥{s['current_price']:.2f}**（由交易撮合决定）")
                warn = initial_price_warning(init_p)
                if warn: st.warning(warn)
                if st.button("💾 保存参数", key=f"sv_{s['id']}", type="primary"):
                    if warn:
                        st.error("参数异常，已阻止保存。请先修正净利润、总股本或行业PE。")
                    else:
                        update_stock_params(s["id"], carbon_price=cp, premium_rate=pr, industry_carbon_mean=icm, revenue=rev, total_shares=ts_, industry_pe=ipe)
                        log_action(st.session_state.username, "stock_params_update", s["symbol"], f"carbon={cp}, premium={pr}, icm={icm}, rev={rev}, ts={ts_}, pe={ipe}")
                    st.rerun()
            with c2:
                if st.button("🗑 删除", key=f"del_{s['id']}"):
                    st.session_state[f"confirm_delete_{s['id']}"] = True; st.rerun()
                if st.session_state.get(f"confirm_delete_{s['id']}"):
                    st.error(f"确认删除 {s['name']} ({s['symbol']})？")
                    dc1, dc2 = st.columns(2)
                    if dc1.button("确认", key=f"cf_del_{s['id']}", type="primary", use_container_width=True):
                        delete_stock(s["id"])
                        log_action(st.session_state.username, "stock_delete", s["symbol"], s["name"])
                        st.session_state[f"confirm_delete_{s['id']}"] = False; st.rerun()
                    if dc2.button("取消", key=f"cx_del_{s['id']}", use_container_width=True):
                        st.session_state[f"confirm_delete_{s['id']}"] = False; st.rerun()

def page_admin_user_mgmt():
    page_header("用户管理", "创建选手、重置密码、启停与批量导入", badge="管理员", ok=True)
    users = get_all_users()
    df = pd.DataFrame(users)
    df["created_at"] = df["created_at"].apply(lambda x: str(x)[:19] if x else "-")
    df["状态"] = df.get("status", "active").fillna("active").map({"active": "正常", "disabled": "已禁用"})
    df.columns = ["ID", "用户名", "角色", "注册时间", "status_col", "状态"]
    df["角色"] = df["角色"].map({"admin": "管理员", "player": "选手"})
    st.markdown('<div class="desktop-table">', unsafe_allow_html=True)
    render_table(df[["ID", "用户名", "角色", "状态", "注册时间"]])
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("""<div style="font-size:14px;font-weight:600;color:#eef2ff;margin:20px 0 12px 0;">操作</div>""", unsafe_allow_html=True)
    with st.expander("创建比赛账号", expanded=False):
        with st.form("admin_create_user"):
            cu1, cu2 = st.columns(2)
            with cu1:
                new_user = st.text_input("用户名", key="new_player_user", placeholder="例如 company_a")
            with cu2:
                new_pwd = st.text_input("初始密码", type="password", key="new_player_pwd", placeholder="至少6位")
            if st.form_submit_button("创建选手账号", type="primary", use_container_width=True):
                u_new = new_user.strip()
                if len(u_new) < 3 or len(new_pwd) < 6:
                    st.warning("用户名至少3位，密码至少6位")
                else:
                    ok, msg = register_user(u_new, new_pwd, "player")
                    if ok:
                        log_action(st.session_state.username, "user_create", u_new, "role=player")
                        st.success("已创建选手账号")
                        st.rerun()
                    else:
                        st.error(msg)

    with st.expander("修改管理员密码", expanded=False):
        with st.form("admin_change_pwd"):
            admin_pwd = st.text_input("新管理员密码", type="password", placeholder="至少8位", key="admin_new_pwd")
            admin_pwd2 = st.text_input("确认新密码", type="password", placeholder="再次输入", key="admin_new_pwd2")
            if st.form_submit_button("更新管理员密码", type="primary", use_container_width=True):
                if len(admin_pwd) < 8:
                    st.warning("管理员密码至少8位")
                elif admin_pwd != admin_pwd2:
                    st.warning("两次密码不一致")
                else:
                    reset_pwd(st.session_state.username, admin_pwd)
                    log_action(st.session_state.username, "admin_password_change", st.session_state.username, "self service")
                    st.success("管理员密码已更新，请妥善保存")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**重置密码**")
        with st.form("reset_pwd"):
            target = st.selectbox("用户", [u["username"] for u in users if u["role"] == "player"], key="rp_user")
            np_ = st.text_input("新密码", type="password", placeholder="至少4位")
            if st.form_submit_button("重置密码", type="primary", use_container_width=True):
                if target and np_ and len(np_) >= 6:
                    reset_pwd(target, np_)
                    log_action(st.session_state.username, "password_reset", target, "admin reset")
                    st.success("已重置"); st.rerun()
                else: st.warning("请完整填写")
    with c2:
        st.markdown("**启用/禁用账户**")
        with st.form("toggle_user"):
            target2 = st.selectbox("用户", [u["username"] for u in users if u["role"] == "player"], key="tg_user")
            cur_status = next((row_get(u, "status", "active") for u in users if u["username"] == target2), "active")
            btn_label = "禁用" if cur_status != "disabled" else "启用"
            if st.form_submit_button(btn_label, type="primary", use_container_width=True):
                toggle_user(target2)
                log_action(st.session_state.username, "user_toggle", target2, btn_label)
                st.success(f"{target2} 已{btn_label}"); st.rerun()

    st.divider()
    st.markdown("**删除账户**")
    with st.form("delete_user"):
        target3 = st.selectbox("选择要删除的用户", [u["username"] for u in users if u["role"] == "player"], key="del_user")
        confirm = st.checkbox("确认删除，此操作不可撤销")
        if st.form_submit_button("删除账户", type="primary", use_container_width=True, disabled=not confirm):
            delete_user(target3)
            st.success(f"{target3} 已删除"); st.rerun()

    st.divider()
    st.markdown("**创建管理员**")
    with st.form("create_admin"):
        aname = st.text_input("管理员用户名", placeholder="至少3位")
        apwd = st.text_input("密码", type="password", placeholder="至少4位")
        if st.form_submit_button("创建管理员", type="primary", use_container_width=True):
            if not aname or not apwd: st.warning("请完整填写")
            elif len(aname) < 3: st.warning("用户名至少3位")
            elif len(apwd) < 4: st.warning("密码至少4位")
            else:
                ok, msg = register_user(aname, apwd, role="admin")
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
                if ok: st.rerun()

    st.divider()
    st.markdown("**批量创建选手（上传 Excel）**")
    st.caption("Excel 第一列=用户名，第二列=密码")
    uploaded = st.file_uploader("选择 Excel 文件", type=["xlsx"], key="batch_upload")
    if uploaded:
        df = pd.read_excel(uploaded)
        if df.shape[1] >= 2:
            valid = [(str(df.iloc[i,0]).strip(), str(df.iloc[i,1]).strip()) for i in range(len(df)) if pd.notna(df.iloc[i,0]) and pd.notna(df.iloc[i,1])]
            st.info(f"检测到 {len(valid)} 个用户")
            confirm_batch_create = st.checkbox("确认批量创建")
            if st.button("一键创建", type="primary", disabled=not confirm_batch_create):
                created, skipped = 0, []
                seen = set()
                for u, p in valid:
                    if u in seen:
                        skipped.append(f"{u}(重复)")
                        continue
                    seen.add(u)
                    ok, msg = register_user(u, p)
                    if ok: created += 1
                    else: skipped.append(f"{u}({msg})")
                result = f"成功创建 {created} 人"
                if skipped: result += f"，跳过 {len(skipped)} 人: {', '.join(skipped[:5])}{'...' if len(skipped)>5 else ''}"
                st.success(result); st.rerun()
        else:
            st.error("Excel 格式错误，至少需要两列（用户名/密码）")

    st.divider()
    st.markdown("**批量删除选手**")
    player_list = [u["username"] for u in users if u["role"] == "player"]
    to_delete = st.multiselect("选择要删除的选手", player_list)
    c1, c2 = st.columns([1, 3])
    with c1:
        confirm_batch = st.checkbox("确认删除")
    with c2:
        if st.button(f"删除 {len(to_delete)} 个选手", type="primary", use_container_width=True, disabled=not confirm_batch or not to_delete):
            for u in to_delete:
                delete_user(u)
            st.success(f"已删除 {len(to_delete)} 人"); st.rerun()

    logs = get_audit_logs()
    if logs:
        st.markdown("""<div style="font-size:14px;font-weight:600;color:#eef2ff;margin:20px 0 12px 0;">最近操作日志</div>""", unsafe_allow_html=True)
        log_df = pd.DataFrame(logs)
        log_df.columns = ["操作者", "动作", "对象", "详情", "时间"]
        render_table(log_df)
def page_admin_settle():
    market_open = is_market_open()
    current_round = get_market_round()
    status = "交易中" if market_open else "已闭市"
    color = "#16a34a" if market_open else "#ef4444"
    page_header("市场控制", f"当前第 {current_round} 轮 · 开闭市、撤销与重置", badge=status, ok=market_open)
    render_competition_strip()

    # 初始化确认状态
    for k in ["cf_close", "cf_open", "cf_undo", "cf_r1"]:
        if k not in st.session_state: st.session_state[k] = False

    st.markdown(f"""
    <div style="background:rgba(10,20,42,.7);border:1px solid rgba(255,255,255,.04);border-radius:12px;padding:24px;text-align:center;margin-bottom:20px;">
        <div style="font-size:13px;color:#94a3b8;">赛程控制状态</div>
        <div style="font-size:36px;font-weight:700;color:{color};margin:8px 0;">{status}</div>
        <div style="font-size:14px;color:#8b949e;">第 {current_round} 轮 · {'选手可提交委托' if market_open else '等待下一轮开市'}</div>
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
                    close_market()
                    log_action(st.session_state.username, "market_close", "round", current_round)
                    st.session_state.cf_close = False; st.session_state.cf_open = False; st.rerun()
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
                    open_market()
                    log_action(st.session_state.username, "market_open", "round", current_round + 1)
                    st.session_state.cf_open = False; st.rerun()
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
                    undo_market()
                    log_action(st.session_state.username, "market_undo", "round", f"{current_round} -> {current_round - 1}")
                    st.session_state.cf_undo = False; st.rerun()
            with cc2:
                if st.button("取消", use_container_width=True):
                    st.session_state.cf_undo = False; st.rerun()

    with c4:
        if not st.session_state.cf_r1:
            if st.button("回到第一轮", use_container_width=True, disabled=current_round <= 1):
                st.session_state.cf_r1 = True; st.session_state.cf_close = False; st.session_state.cf_open = False; st.session_state.cf_undo = False; st.rerun()
        else:
            st.warning("确认重开赛局？将清空所有交易、持仓和K线历史，资金与股票价格回到初始状态。")
            cc1, cc2 = st.columns(2)
            with cc1:
                if st.button("确认重置", type="primary", use_container_width=True):
                    reset_to_round1(); st.session_state.cf_r1 = False
                    log_action(st.session_state.username, "market_reset_round1", "round", 1)
                    # 清除所有会话缓存，强制从DB读取
                    for k in list(st.session_state.keys()):
                        if k.startswith("nav_main_") or k.startswith("kline_round"):
                            del st.session_state[k]
                    st.success("已重置赛局，所有数据已清空"); st.rerun()
            with cc2:
                if st.button("取消", use_container_width=True):
                    st.session_state.cf_r1 = False; st.rerun()

    # 数据库备份
    with st.expander("数据库备份"):
        download_db_button()

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
            render_table(df[["round","开盘","最高","最低","收盘","涨跌幅","成交量"]].rename(columns={"round":"轮次"}))
        else:
            st.info("暂无K线数据")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 导航 + main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NAV = {
    "总览": page_overview, "交易大厅": page_trade_hall,
    "我的持仓": page_portfolio, "交易记录": page_market_making,
    "K线展板": page_kline,     "专业K线": page_kline_tradingview,
    "市场控制": page_admin_settle,
    "股票汇总": page_admin_stock_summary,
    "股票管理": page_admin_stock_mgmt, "用户管理": page_admin_user_mgmt,
}
PLAYER_NAV = ["总览", "交易大厅", "我的持仓", "交易记录", "K线展板", "专业K线"]
ADMIN_NAV = ["市场控制", "股票汇总", "股票管理", "用户管理", "K线展板", "专业K线"]

st.set_page_config(page_title="Gipfel - 智能投资分析系统", layout="wide", initial_sidebar_state="expanded")
st.markdown(RESPONSIVE_CSS + SIDEBAR_CSS, unsafe_allow_html=True)
if "db_initialized" not in st.session_state:
    init_db()
    st.session_state.db_initialized = True

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False; st.session_state.username = ""; st.session_state.role = ""

def main():
    if not st.session_state.logged_in:
        # 未登录时显示公开行情大屏，点击"登录交易"才显示登录页
        if st.session_state.get("show_login"):
            page_login()
        else:
            page_public_dashboard()
        return
    nav = ADMIN_NAV if st.session_state.role == "admin" else PLAYER_NAV
    if st.session_state.get("nav_current") not in nav:
        st.session_state.nav_current = nav[0]
    with st.sidebar:
        role_text = "管理员" if st.session_state.role == "admin" else "选手"
        bal = get_user_balance(st.session_state.username)
        bal_text = f" ｜ {fmt_money(bal)}" if st.session_state.role == "player" else ""
        st.markdown(f"""
        <div class="sb-brand"><div class="name">Gipfel</div><div class="sub">INSIGHT+</div></div>
        <div class="sb-user"><div class="uname">{esc(st.session_state.username)}</div><div class="urole"><span class="dot"></span>{role_text}{bal_text}</div></div>
        """, unsafe_allow_html=True)
        st.markdown('<div class="menu-group-label">导航</div>', unsafe_allow_html=True)
        sel = st.session_state.nav_current
        for n in nav:
            is_active = (n == sel)
            tp = "primary" if is_active else "secondary"
            if st.button(n, key=f"ns_{n}", type=tp, use_container_width=True):
                st.session_state.nav_current = n
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        if st.button("退出登录", key="sb_exit", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.username = ""
            st.session_state.role = ""
    # 桌面端固定导航：不依赖 Streamlit 原生 sidebar，避免部署端折叠后找不到导航
    role_text = "管理员" if st.session_state.role == "admin" else "选手"
    bal = get_user_balance(st.session_state.username)
    bal_text = f" ｜ {fmt_money(bal)}" if st.session_state.role == "player" else ""
    with st.container(key="desktop_nav_bar"):
        st.markdown(f"""
        <div class="desktop-nav-brand">
            <div class="name">Gipfel</div>
            <div class="sub">INSIGHT+</div>
        </div>
        <div class="desktop-nav-user">
            <div class="uname">{esc(st.session_state.username)}</div>
            <div class="role"><span class="dot"></span>{role_text}{bal_text}</div>
        </div>
        <div class="desktop-nav-label">导航</div>
        """, unsafe_allow_html=True)
        for n in nav:
            tp = "primary" if n == st.session_state.nav_current else "secondary"
            if st.button(n, key=f"dn_{n}", type=tp, use_container_width=True):
                st.session_state.nav_current = n
        st.markdown('<div class="desktop-nav-spacer"></div>', unsafe_allow_html=True)
        if st.button("退出登录", key="dn_exit", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.username = ""
            st.session_state.role = ""
    # 移动端底部导航（仅玩家）
    sel = st.session_state.nav_current
    if st.session_state.role == "player" and sel in PLAYER_NAV:
        short = {"总览": "总览", "交易大厅": "交易", "我的持仓": "持仓", "交易记录": "记录", "K线展板": "K线", "专业K线": "实时"}
        with st.container(key="mobile_nav_bar"):
            st.markdown('<div class="mob-nav-scroll">', unsafe_allow_html=True)
            mm = st.columns(len(PLAYER_NAV))
            for mi, mn in enumerate(PLAYER_NAV):
                with mm[mi]:
                    tp = "primary" if mn == sel else "secondary"
                    if st.button(short.get(mn,mn), key=f"mb_{mn}", type=tp, use_container_width=True):
                        st.session_state.nav_current = mn
            st.markdown('</div>', unsafe_allow_html=True)
    sel = st.session_state.nav_current
    if sel in NAV: NAV[sel]()
    else: page_overview()

if __name__ == "__main__":
    main()
