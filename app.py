"""
股票交易系统 — 移动端优先响应式版本
商业模拟挑战赛 · 零图标纯文字 · 触屏友好
"""
import os, sqlite3, hashlib, secrets
from contextlib import contextmanager
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "stock_analysis.db")

@contextmanager
def get_db_cm():
    """带异常安全的数据库连接上下文管理器"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
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
    os.makedirs(DB_DIR, exist_ok=True)
    with get_db_cm() as conn:
        cur = conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT DEFAULT 'player', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT 'active', balance REAL DEFAULT 1000000);
            CREATE TABLE IF NOT EXISTS stocks(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT UNIQUE NOT NULL, name TEXT NOT NULL, current_price REAL DEFAULT 0, previous_close REAL DEFAULT 0, is_deleted INTEGER DEFAULT 0, total_shares REAL DEFAULT 10000, industry_pe REAL DEFAULT 20, carbon_price REAL DEFAULT 50, industry_carbon_mean REAL DEFAULT 50, premium_rate REAL DEFAULT 50, init_funds REAL DEFAULT 5000, last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, stock_symbol TEXT NOT NULL, trade_type TEXT NOT NULL, price REAL NOT NULL, shares INTEGER NOT NULL, round INTEGER DEFAULT 0, trade_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS kline(id INTEGER PRIMARY KEY AUTOINCREMENT, stock_symbol TEXT NOT NULL, round INTEGER DEFAULT 0, open_price REAL DEFAULT 0, high_price REAL DEFAULT 0, low_price REAL DEFAULT 0, close_price REAL DEFAULT 0, volume REAL DEFAULT 0, buy_total REAL DEFAULT 0, sell_total REAL DEFAULT 0, change_pct REAL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS rounds(stock_symbol TEXT NOT NULL, round INTEGER DEFAULT 0, is_settled INTEGER DEFAULT 0, PRIMARY KEY(stock_symbol, round));
            CREATE TABLE IF NOT EXISTS market_state(id INTEGER PRIMARY KEY CHECK(id=1), state TEXT DEFAULT 'open', round INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT DEFAULT '', detail TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS login_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, attempt_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS order_book(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, stock_symbol TEXT NOT NULL, trade_type TEXT NOT NULL, price REAL NOT NULL, shares INTEGER NOT NULL, round INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        """)
        conn.commit()
        # 迁移：revenue 字段
        try: cur.execute("ALTER TABLE stocks ADD COLUMN revenue REAL DEFAULT 100000")
        except: pass
        cur.execute("UPDATE stocks SET revenue=100000 WHERE revenue IS NULL OR revenue=0")
        conn.execute("INSERT OR IGNORE INTO market_state(id,state,round) VALUES(?,?,?)", (1, 'open', 1))
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
                price = calc_initial_price(rev, ts_, ipe)
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
                    conn.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES(?,?,1)", (sym, r))
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
                conn.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES(?,?,0)", (s["symbol"], next_round))
            conn.commit()
        else:
            # 非首次：市场开盘时同步 market_state.round 与真实未结算轮次。
            state_row = conn.execute("SELECT state,round FROM market_state WHERE id=1").fetchone()
            market_state = state_row["state"] if state_row else "open"
            active_symbols = [s["symbol"] for s in conn.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall()]
            if market_state == "open":
                open_rounds = [
                    r["round"] for r in conn.execute(
                        "SELECT DISTINCT round FROM rounds WHERE is_settled=0 ORDER BY round"
                    ).fetchall()
                ]
                if open_rounds:
                    target_round = max(open_rounds)
                else:
                    max_k = conn.execute("SELECT COALESCE(MAX(round),0) FROM kline").fetchone()[0]
                    target_round = max(max_k + 1, state_row["round"] if state_row else 1)
                for sym in active_symbols:
                    conn.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES(?,?,0)", (sym, target_round))
                conn.execute("UPDATE market_state SET round=? WHERE id=1", (target_round,))
            else:
                max_k = conn.execute("SELECT COALESCE(MAX(round),0) FROM kline").fetchone()[0]
                if max_k:
                    conn.execute("UPDATE market_state SET round=? WHERE id=1", (max_k,))
            conn.commit()

def _seed(conn):
    cur = conn.cursor()
    admin_pw = get_admin_password() or "admin123"
    cur.execute("INSERT INTO users(id,username,password,role,created_at,status,balance) VALUES(?,?,?,'admin',CURRENT_TIMESTAMP,'active',1000000)", (1, "admin", make_pwd(admin_pw)))
    for i, u in enumerate(["player1", "player2", "player3"], 2):
        cur.execute("INSERT INTO users(id,username,password,role,created_at,status,balance) VALUES(?,?,?,'player',CURRENT_TIMESTAMP,'active',1000000)", (i, u, make_pwd(u)))
    for sym, name, ts_, rev, ipe, cp, icm, pr, funds in [
        ("WULIU", "物流1公司", 10000, 200, 20, 50, 50, 50, 2000),
        ("JXIAO", "经销1公司", 10000, 300, 20, 50, 50, 50, 3000),
        ("JGONG", "加工1公司", 10000, 400, 20, 50, 50, 50, 4000),
        ("YLIAO", "原料1公司", 10000, 500, 20, 50, 50, 50, 5000),
    ]:
        price = calc_initial_price(rev, ts_, ipe)
        cur.execute("""INSERT INTO stocks(symbol,name,current_price,previous_close,init_funds,
            total_shares,revenue,industry_pe,carbon_price,industry_carbon_mean,premium_rate)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (sym, name, price, price, funds, ts_, rev, ipe, cp, icm, pr))
        cur.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES(?,1,0)", (sym,))
    trades = [("player1", "WULIU", "buy", 9.5, 200, 1), ("player1", "JXIAO", "sell", 14.0, 100, 1), ("player1", "WULIU", "sell", 10.5, 80, 1), ("player2", "JGONG", "buy", 19.0, 150, 1), ("player2", "JXIAO", "sell", 16.0, 60, 1), ("player3", "WULIU", "buy", 10.0, 100, 1), ("player3", "YLIAO", "buy", 24.0, 80, 1), ("player2", "YLIAO", "buy", 26.0, 50, 1), ("player3", "JGONG", "sell", 21.0, 40, 1)]
    for args in trades: cur.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,?,?,?,?)", args)
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

def calc_initial_price(revenue, total_shares, industry_pe):
    """初始价 = 净利润×10000÷总股本÷行业PE（Excel公式）"""
    if not total_shares or not industry_pe or not revenue:
        return 0
    return round(revenue * 10000 / total_shares / industry_pe, 2)

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
        cur = conn.cursor()
        stock = dict(cur.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone())
        r = cur.execute("SELECT MIN(round) FROM rounds WHERE stock_symbol=? AND is_settled=0", (symbol,)).fetchone()
        cr = r[0] if r and r[0] else 0
        if cr == 0: return None, False, 0, 0, 0, 0, 0
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
        pf = round(1 + 0.2 * (row_get(stock, "premium_rate", 50) - 50) / 50, 4)
        icm = max(row_get(stock, "industry_carbon_mean", 50), 1)
        cf = round(1 - 0.5 * (row_get(stock, "carbon_price", 50) - icm) / icm, 4)
        np_ = compute_price(dict(stock, buy_total=bt, sell_total=st_amt))
        raw = round((stock["previous_close"] or stock["current_price"]), 2) * (bt / max(st_amt, 1)) * pf * cf
        pc = stock["previous_close"] or stock["current_price"]
        cpct = round((np_ - pc) / pc * 100, 2) if pc else 0
        hi = max(np_, pc); lo = min(np_, pc)
        cur.execute("DELETE FROM kline WHERE stock_symbol=? AND round=?", (symbol, cr))
        cur.execute("INSERT INTO kline(stock_symbol,round,open_price,high_price,low_price,close_price,volume,buy_total,sell_total,change_pct) VALUES(?,?,?,?,?,?,?,?,?,?)", (symbol, cr, pc, hi, lo, np_, tv, bt, st_amt, cpct))
        nr = cr + 1
        cur.execute("UPDATE stocks SET previous_close=?,current_price=? WHERE symbol=?", (np_, np_, symbol))
        cur.execute("UPDATE rounds SET is_settled=1 WHERE stock_symbol=? AND round=?", (symbol, cr))
        conn.commit()
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
    except sqlite3.IntegrityError:
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
    """直接读库，WAL模式+缓存已足够支撑高并发"""
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
            price = calc_initial_price(revenue, total_shares, industry_pe)
            funds = price * 10000 * 20 / 10000
            conn.execute("INSERT INTO stocks(symbol,name,current_price,previous_close,init_funds,total_shares,revenue,industry_pe) VALUES(?,?,?,?,?,?,?,?)",
                (sym.upper(), name, price, price, funds, total_shares, revenue, industry_pe))
            conn.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES(?,1,1)", (sym.upper(),))
            conn.commit()
        try: get_public_quote_snapshot.clear()
        except: pass
        return True, f"添加成功，初始价={price}"
    except sqlite3.IntegrityError:
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

def delete_stock(sid):
    with get_db_cm() as conn:
        conn.execute("UPDATE stocks SET is_deleted=1 WHERE id=?", (sid,))
        conn.commit()
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
        conn.execute("BEGIN IMMEDIATE")
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
        conn.execute("BEGIN IMMEDIATE")
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

def open_market():
    with get_db_cm() as conn:
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute("SELECT state,round FROM market_state WHERE id=1").fetchone()
        if not r or r["state"] == "open": return
        new_round = r["round"] + 1
        stocks = conn.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall()
        for s in stocks:
            conn.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES(?,?,0)", (s["symbol"], new_round))
        conn.execute("UPDATE market_state SET state='open', round=? WHERE id=1", (new_round,))
        conn.commit()

    try:
        get_public_quote_snapshot.clear()
    except Exception:
        pass

def undo_market():
    """撤销上一轮：回退到闭市前状态"""
    with get_db_cm() as conn:
        conn.execute("BEGIN IMMEDIATE")
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

def reset_to_round1():
    """重开赛局：清空交易/K线/轮次，价格和资金回到初始状态，从第1轮重新开始。"""
    with get_db_cm() as conn:
        conn.execute("BEGIN IMMEDIATE")
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

def get_kline_data(symbol):
    """K线数据 + 实时追加当前未结算轮次"""
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

        # 查找当前未结算轮次（选手正在交易的轮次）
        open_r = conn.execute("SELECT MIN(round) FROM rounds WHERE stock_symbol=? AND is_settled=0", (symbol,)).fetchone()
        cr = open_r[0] if open_r and open_r[0] else 0
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
    klines = get_kline_data(symbol)
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 响应式 CSS — 移动端优先
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESPONSIVE_CSS = """
<style>
* { box-sizing: border-box; }
html, body, .stApp {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", sans-serif;
    -webkit-font-smoothing: antialiased;
}
#MainMenu, .stDeployButton, footer, [data-testid="stStatusWidget"],
[data-testid="stDecoration"], [data-testid="stToolbar"],
[data-testid="manage-app-button"] { display: none !important; }

.stApp { background: #070b13 !important; }
section.main > div.block-container { padding: 6px 16px !important; }

/* 顶栏 */
.topbar {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0 0 6px 0; font-size: 11px; color: #5a6a7e;
    border-bottom: 1px solid #1a2332; margin-bottom: 12px;
}
.topbar .brand { font-size: 18px; font-weight: 700; color: #e2e8f0; letter-spacing: .5px; }

/* KPI 卡片 */
.kpi-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 14px; }
.kpi-card {
    background: #0f1724; border-radius: 8px; padding: 14px 16px;
    border: 1px solid #1e2a3a;
}
.kpi-card .label { font-size: 10px; color: #5a6a7e; margin-bottom: 2px; letter-spacing: .5px; }
.kpi-card .value { font-size: 22px; font-weight: 700; color: #e2e8f0; font-feature-settings: "tnum"; }
.kpi-card .delta { font-size: 11px; margin-top: 1px; }
.kpi-card .delta.up { color: #f23645; }
.kpi-card .delta.down { color: #089981; }

/* 股票卡片 */
.stock-card {
    background: #0f1724; border-radius: 8px; padding: 12px 14px;
    margin-bottom: 6px; border: 1px solid #1e2a3a;
}
.stock-card .sc-header { display: flex; justify-content: space-between; align-items: center; }
.stock-card .sc-name { font-size: 14px; font-weight: 600; color: #e2e8f0; }
.stock-card .sc-pct { font-size: 13px; font-weight: 600; }
.stock-card .sc-pct.up { color: #f23645; }
.stock-card .sc-pct.down { color: #089981; }
.stock-card .sc-detail {
    display: grid; grid-template-columns: 1fr 1fr; gap: 2px 12px;
    margin: 4px 0; font-size: 12px; color: #5a6a7e;
}
.stock-card .sc-detail .val { color: #e2e8f0; font-weight: 500; }

.section-title { font-size: 13px; font-weight: 700; color: #e2e8f0; margin-bottom: 6px; letter-spacing: .3px; }
.chart-summary { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; margin: 6px 0; }
.chart-metric { background: #0f1724; border: 1px solid #1e2a3a; border-radius: 6px; padding: 8px 10px; }
.chart-metric .label { font-size: 10px; color: #5a6a7e; }
.chart-metric .value { font-size: 14px; font-weight: 700; color: #e2e8f0; }
.chart-metric .value.up { color: #f23645; }
.chart-metric .value.down { color: #089981; }
.chart-panel { background: #0f1724; border: 1px solid #1e2a3a; border-radius: 8px; padding: 4px; }

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
.desktop-only { display: none; }
.mobile-only { display: block; }

@media (min-width: 768px) {
    section.main > div.block-container { padding: 12px 22px !important; max-width: 1400px !important; margin: 0 auto !important; }
    .kpi-grid { grid-template-columns: repeat(4, 1fr); gap: 10px; }
    .desktop-only { display: block; }
    .mobile-only { display: none; }
    .chart-summary { grid-template-columns: repeat(4, 1fr); gap: 6px; }
    .desktop-table { background: #0f1724; border-radius: 8px; padding: 0 12px 8px 12px; border: 1px solid #1e2a3a; }
    [data-testid="stDataFrame"] { border: none !important; }
    [data-testid="stDataFrame"] th {
        background: transparent !important; font-size: 10px !important;
        color: #5a6a7e !important; font-weight: 600 !important;
        border-bottom: 1px solid #1a2332 !important; padding: 6px !important;
    }
    [data-testid="stDataFrame"] td {
        font-size: 13px !important; color: #e2e8f0 !important;
        border-bottom: 1px solid #1a2332 !important; padding: 6px !important;
    }
    div[data-testid="stButton"] button {
        border-radius: 6px !important; font-weight: 500 !important;
        background: #1a2332 !important; border: 1px solid #1e2a3a !important;
        color: #94a3b8 !important; font-size: 13px !important; padding: 4px 14px !important;
    }
    div[data-testid="stButton"] button:hover { border-color: #f23645 !important; color: #e2e8f0 !important; }
    div[data-testid="stButton"] button[kind="primary"] {
        background: #f23645 !important; border: none !important; color: #fff !important;
    }
    div[data-testid="stButton"] button[kind="primary"]:hover {
        background: #d42d3b !important;
    }
    .st-emotion-cache-1aej4i3, details {
        background: #0f1724 !important; border: 1px solid #1e2a3a !important;
        border-radius: 8px !important; margin-bottom: 4px !important;
    }
    .st-emotion-cache-1aej4i3 summary, details summary {
        font-weight: 500 !important; padding: 8px 12px !important; color: #e2e8f0 !important;
    }
    input, div[data-baseweb="input"] input {
        background: #070b13 !important; border: 1px solid #1e2a3a !important;
        color: #e2e8f0 !important; border-radius: 6px !important;
        padding: 6px 10px !important; font-size: 13px !important;
    }
    input:focus { border-color: #f23645 !important; box-shadow: 0 0 0 2px rgba(242,54,69,.1) !important; }
}

@media (min-width: 768px) {
    [data-testid="stSidebarNav"] { display: none !important; }
}
</style>
"""

SIDEBAR_CSS = """
<style>
    section[data-testid="stSidebar"] { background: #070b13 !important; border-right: 1px solid #1a2332 !important; }
    [data-testid="stSidebarNav"] { display: none !important; }
    [data-testid="stStatusWidget"] { display: none !important; }
    .stDeployButton, footer, #MainMenu, [data-testid="stToolbar"],
    [data-testid="stDecoration"], [data-testid="manage-app-button"] { display: none !important; }

    .sb-brand { padding: 20px 22px 12px 22px; border-bottom: 1px solid #1a2332; }
    .sb-brand .name p { font-size: 24px !important; font-weight: 800 !important; color: #f1f5f9 !important; margin: 0 !important; letter-spacing: 2px !important; }
    .sb-brand .sub p { color: #475569 !important; font-size: 10px !important; letter-spacing: 4px !important; margin: 2px 0 0 0 !important; }
    .sb-user { padding: 12px 22px 10px 22px; border-bottom: 1px solid #1a2332; }
    .sb-user .uname p { font-size: 14px !important; font-weight: 600 !important; color: #f1f5f9 !important; margin: 0 !important; }
    .sb-user .urole p { font-size: 12px !important; color: #475569 !important; margin: 0 !important; }
    .sb-user .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #089981; margin-right: 4px; vertical-align: middle; }
    .menu-group-label { padding: 14px 22px 4px 22px; }
    .menu-group-label p { font-size: 10px !important; font-weight: 700 !important; color: #475569 !important; text-transform: uppercase; letter-spacing: 2px !important; margin: 0 !important; }

    section[data-testid="stSidebar"] div[role="radiogroup"] { padding: 2px 8px !important; }
    section[data-testid="stSidebar"] div[role="radiogroup"] label {
        padding: 10px 14px !important;
        font-size: 14px !important; font-weight: 500 !important;
        color: #94a3b8 !important;
        background: transparent !important;
        border: none !important;
        border-radius: 6px !important;
        cursor: pointer !important;
        letter-spacing: .3px !important;
        position: relative !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
        background: rgba(255,255,255,.04) !important;
        color: #e2e8f0 !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label[data-checked="true"] {
        background: rgba(242,54,69,.08) !important;
        color: #f23645 !important;
        font-weight: 600 !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label[data-checked="true"]::before {
        content: '' !important; position: absolute !important;
        left: -1px !important; top: 20% !important; bottom: 20% !important;
        width: 3px !important;
        background: #f23645 !important;
        border-radius: 0 2px 2px 0 !important;
        pointer-events: none !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label input { display: none !important; }
    section[data-testid="stSidebar"] div[role="radiogroup"] label div[data-testid="stMarkdownContainer"] p {
        margin: 0 !important; font-size: 14px !important; color: inherit !important;
    }
    section[data-testid="stSidebar"] button:last-of-type { margin: 4px 12px !important; width: calc(100% - 24px) !important; }
</style>
"""

DASHBOARD_CSS = """
<style>
    .stApp { background: #080c17 !important; }
    section.main > div.block-container { padding: 12px 20px !important; max-width: 1400px !important; margin: 0 auto !important; }
    #MainMenu, .stDeployButton, footer, [data-testid="stStatusWidget"],
    [data-testid="stDecoration"], [data-testid="stToolbar"], header { display: none !important; }

    .dash-top { display: flex; justify-content: space-between; align-items: center; padding: 8px 0 16px 0; }
    .dash-brand { font-size: 28px; font-weight: 800; letter-spacing: 5px;
        background: linear-gradient(135deg, #f0e6d3, #d4a853);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
    .dash-sub { font-size: 11px; color: rgba(255,255,255,.3); letter-spacing: 3px; text-transform: uppercase; margin-top: 2px; }
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

    .stock-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
    .s-card {
        background: linear-gradient(135deg, rgba(255,255,255,.04), rgba(255,255,255,.01));
        border: 1px solid rgba(255,255,255,.08); border-radius: 12px;
        padding: 16px 18px; position: relative; overflow: hidden; transition: border-color .2s;
        cursor: default;
    }
    .s-card:hover { border-color: rgba(255,255,255,.15); }
    .s-card .sym { font-size: 11px; color: rgba(255,255,255,.35); letter-spacing: 1px; text-transform: uppercase; }
    .s-card .nm { font-size: 15px; font-weight: 600; color: rgba(255,255,255,.85); margin: 2px 0 6px 0; }
    .s-card .pr { font-size: 30px; font-weight: 700; font-feature-settings: "tnum"; }
    .s-card .pr.up { color: #ef5350; } .s-card .pr.down { color: #2ecc71; }
    .s-card .chg { font-size: 13px; margin-top: 2px; font-weight: 500; }
    .s-card .chg.up { color: #ef5350; } .s-card .chg.down { color: #2ecc71; }
    .s-card .extra { font-size: 11px; color: rgba(255,255,255,.25); margin-top: 6px; font-family: monospace; }
    .s-card::after { content: ''; position: absolute; bottom: 0; left: 18px; right: 18px; height: 2px; border-radius: 2px 2px 0 0; }
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
    .dash-ft { display: flex; justify-content: space-between; padding: 10px 0 0 0;
        border-top: 1px solid rgba(255,255,255,.06); margin-top: 4px;
        font-size: 11px; color: rgba(255,255,255,.18); font-family: monospace; }

    .login-btn { padding: 6px 18px; border-radius: 8px; font-size: 13px; font-weight: 500;
        cursor: pointer; border: 1px solid rgba(255,255,255,.15); background: transparent;
        color: rgba(255,255,255,.5); font-family: inherit; text-decoration: none; transition: all .15s;
        display: inline-block; text-align: center; }
    .login-btn:hover { background: rgba(255,255,255,.08); color: rgba(255,255,255,.8); }

    @media (max-width: 768px) { .stock-grid { grid-template-columns: repeat(2, 1fr); } .s-card .pr { font-size: 22px; } }
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
    c1, c2, c3 = st.columns([3, 2, 1])
    with c1:
        st.markdown(f'<div class="dash-brand">双镜</div><div class="dash-sub">智能投资分析系统</div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="dash-clock" style="text-align:center;" id="liveClock"></div>', unsafe_allow_html=True)
    with c3:
        if st.button("登录交易", key="dash_login_btn"):
            st.session_state.show_login = True
            st.rerun()

    # 市场状态条
    st.markdown(f'<div class="mkt-bar"><span class="mkt-dot {mkt_cls}"></span><span class="mkt-text">市场 <strong>{mkt_text}</strong> ｜ 第 <strong>{mkt_round}</strong> 轮</span><span class="mkt-round" id="liveClockMkt"></span></div>', unsafe_allow_html=True)

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
        q = quotes.get(s["symbol"]) or get_market_card_data(s)
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
            <div class="pr {cls}">¥{p:,.2f}</div>
            <div class="chg {cls}">{sign}{chg:,.2f} ({sign}{pct:.2f}%)</div>
            <div class="extra">当前第 {mkt_round} 轮 ｜ 最新成交第 {q['round']} 轮</div>
            <div class="extra">参考 ¥{prev:,.2f} ｜ 近5轮量 {q['volume5']:,}</div>
            <div class="extra">近5轮买额 ¥{q['buy5']:,.0f} ｜ 卖额 ¥{q['sell5']:,.0f}</div>
        </div>"""
    st.markdown(f'<div class="stock-grid">{cards}</div>', unsafe_allow_html=True)

    # 股票选择 + K线图
    st.markdown('<div class="tab-row">', unsafe_allow_html=True)
    cols = st.columns([1] * len(stocks))
    if "dash_sym" not in st.session_state:
        st.session_state.dash_sym = stocks[0]["symbol"]
    for i, s in enumerate(stocks):
        active = "active" if st.session_state.dash_sym == s["symbol"] else ""
        if cols[i].button(f"{s['name']}", key=f"tab_{s['symbol']}", use_container_width=True):
            st.session_state.dash_sym = s["symbol"]
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    # K线图
    sym = st.session_state.dash_sym
    data = get_kline_data(sym)
    if data:
        df_k = pd.DataFrame(data).sort_values("round").reset_index(drop=True)
        df_k["x"] = df_k["round"].apply(lambda r: f"第{r}轮")

        RED_UP = "#ef5350"
        GREEN_DN = "#2ecc71"
        up_mask = df_k["close_price"] >= df_k["open_price"]
        vol_c = ["rgba(239,83,80,0.4)" if u else "rgba(46,204,113,0.4)" for u in up_mask]

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25])
        fig.add_trace(go.Candlestick(x=df_k["x"], open=df_k["open_price"], high=df_k["high_price"],
            low=df_k["low_price"], close=df_k["close_price"],
            increasing=dict(line=dict(color=RED_UP, width=1.2), fillcolor=RED_UP),
            decreasing=dict(line=dict(color=GREEN_DN, width=1.2), fillcolor=GREEN_DN),
            whiskerwidth=0.5, name="", showlegend=False), row=1, col=1)
        fig.add_trace(go.Bar(x=df_k["x"], y=df_k["volume"], marker_color=vol_c, name="", showlegend=False), row=2, col=1)

        for period, color, name in [(5, "#f59e0b", "MA5"), (10, "#a78bfa", "MA10")]:
            if len(df_k) >= period:
                ma = df_k["close_price"].rolling(period).mean()
                fig.add_trace(go.Scatter(x=df_k["x"], y=ma, mode="lines", line=dict(color=color, width=1.2), name=name), row=1, col=1)

        fig.update_layout(height=520, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=8, b=8, l=0, r=12), xaxis_rangeslider_visible=False,
            font=dict(color="rgba(255,255,255,.5)", size=10), hovermode="x unified",
            hoverlabel=dict(bgcolor="#1e293b", font_size=12, font_color="#ffffff"),
            xaxis=dict(showspikes=True, spikemode="across", spikecolor="rgba(255,255,255,.1)"),
            yaxis=dict(showspikes=True, spikecolor="rgba(255,255,255,.1)"),
            showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.00, xanchor="left", x=0,
                font=dict(color="rgba(255,255,255,.4)", size=10)),
            bargap=0.1)
        fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,.05)", griddash="dot",
            side="right", row=1, col=1, zeroline=False, tickfont=dict(size=10))
        fig.update_xaxes(showgrid=False, row=1, col=1)
        fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,.05)", griddash="dot",
            side="right", row=2, col=1, zeroline=False, tickfont=dict(size=9))

        st.markdown('<div class="chart-box">', unsafe_allow_html=True)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False, "scrollZoom": True})
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="color:rgba(255,255,255,.3);text-align:center;padding:30px;">暂无K线数据</div>', unsafe_allow_html=True)

    # 底部
    st.markdown(f'<div class="dash-ft"><span>双镜 · 智能投资分析系统</span><span>数据每5秒刷新 · 仅供模拟参考</span></div>', unsafe_allow_html=True)

    # 自动刷新（仅公开行情页，不影响登录态）
    col1, col2, col3 = st.columns([3, 2, 3])
    with col2:
        if st.button("🔄 刷新", use_container_width=True):
            st.rerun()
    st.markdown("""
    <div style="text-align:center;color:rgba(255,255,255,.08);font-size:10px;font-family:monospace;">每30秒自动刷新 · 点击「🔄 刷新」立即更新</div>
    <script>
    setTimeout(function(){ window.location.reload(); }, 30000);
    </script>
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
        .stApp > header { height: 0 !important; overflow: hidden; }
        div[data-testid="stToolbar"] { visibility: hidden; }
        section.main > div.block-container { padding: 0 !important; }
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
            -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;">双镜</h1>
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
    st.markdown('<p style="text-align:center;color:rgba(255,255,255,.1);font-size:10px;margin-top:20px;">双镜 · 智能投资分析系统</p>', unsafe_allow_html=True)

def fmt_money(v):   return f"¥{v:,.0f}"
def fmt_pnl(v):     return f"¥{v:,.2f}"
def fmt_pct(v, s=True):
    sign = "+" if (s and v > 0) else ("" if s else "")
    return f"{sign}{v:,.2f}%"

def fmt_num(v):     return f"{v:,}"

def download_db_button():
    """管理员一键导出数据库按钮"""
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "rb") as f:
            st.download_button(
                label="下载数据库备份",
                data=f,
                file_name=f"stock_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                mime="application/octet-stream",
                use_container_width=True,
            )

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
            <div style="font-size:20px;font-weight:700;color:#eef2ff;">{fmt_money(total_mv)}</div>
        </div>
        <div style="background:rgba(10,20,42,.7);border:1px solid rgba(255,255,255,.04);border-radius:10px;padding:10px 18px;flex:1;min-width:100px;">
            <div style="font-size:11px;color:#64748b;">总盈亏</div>
            <div style="font-size:20px;font-weight:700;color:{pnl_color(total_pnl)};">{fmt_money(total_pnl)}</div>
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
    st.dataframe(d[["名称", "代码", "持仓", "成本", "现价", "市值", "盈亏", "收益率"]], use_container_width=True, hide_index=True)
    st.markdown('</div></div>', unsafe_allow_html=True)

    # 买入时间（从最近一笔买入记录取）
    with get_db_cm() as conn:
        times = conn.execute("SELECT DISTINCT stock_symbol, MAX(trade_date) as t FROM transactions WHERE username=? AND trade_type='buy' GROUP BY stock_symbol ORDER BY t DESC", (st.session_state.username,)).fetchall()
    if times:
        st.caption(f"最近买入：{times[0]['stock_symbol']} {str(times[0]['t'])[:16]}" if times[0]['t'] else "")

def page_market_making():
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{esc(st.session_state.username)}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#eef2ff;margin-bottom:12px">交易记录</div>""", unsafe_allow_html=True)
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
    df = pd.DataFrame([{
        "股票": r["nm"], "类型": "买入" if r["trade_type"]=="buy" else "卖出",
        "价格": f"¥{r['price']:.2f}", "数量": f"{r['shares']:,}股",
        "金额": f"¥{r['price']*r['shares']:,.2f}", "轮次": f"第{r['round']}轮",
        "时间": str(r["trade_date"])[:19] if r["trade_date"] else "-",
    } for r in rows])
    st.dataframe(df, use_container_width=True, hide_index=True)

def page_trade_hall():
    stocks = get_stocks()
    if not stocks: st.error("无股票"); return
    mkt_open = is_market_open()
    mkt_round = get_market_round()
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{esc(st.session_state.username)}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#eef2ff;margin-bottom:12px">交易大厅</div>""", unsafe_allow_html=True)

    if not mkt_open:
        st.warning("市场已闭市，无法交易。等待管理员开市。")
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

def page_kline():
    stocks = get_stocks()
    if not stocks: st.info("无数据"); return
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{esc(st.session_state.username)}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div class="section-title">K 线展板</div>""", unsafe_allow_html=True)
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
    x_values = df_k["x_label"]

    latest = df_k.iloc[-1]
    first_open = float(df_k.iloc[0]["open_price"])
    latest_close = float(latest["close_price"])
    total_change = round((latest_close - first_open) / first_open * 100, 2) if first_open else 0
    latest_change = float(latest.get("change_pct", 0) or 0)
    total_volume = int(df_k["volume"].sum())
    high_price = float(df_k["high_price"].max())
    low_price = float(df_k["low_price"].min())
    latest_cls = "up" if latest_change >= 0 else "down"
    total_cls = "up" if total_change >= 0 else "down"
    st.markdown(f"""
    <div class="chart-summary">
        <div class="chart-metric"><div class="label">最新收盘</div><div class="value {latest_cls}">{fmt_money(latest_close)}</div></div>
        <div class="chart-metric"><div class="label">本轮涨跌</div><div class="value {latest_cls}">{latest_change:+.2f}%</div></div>
        <div class="chart-metric"><div class="label">区间涨跌</div><div class="value {total_cls}">{total_change:+.2f}%</div></div>
        <div class="chart-metric"><div class="label">区间成交量</div><div class="value">{fmt_num(total_volume)}</div></div>
    </div>
    """, unsafe_allow_html=True)

    # ── A股标准色：红涨绿跌（实体填充） ──
    RED_UP   = "#ef5350"   # 阳线红
    GREEN_DN = "#2ecc71"   # 阴线绿
    up_mask  = df_k["close_price"] >= df_k["open_price"]
    vol_color = ["rgba(239,83,80,0.4)" if u else "rgba(46,204,113,0.4)" for u in up_mask]

    # ── 主图蜡烛 + 成交量副图 ──
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.03, row_heights=[0.75, 0.25])

    fig.add_trace(go.Candlestick(
        x=x_values, open=df_k["open_price"], high=df_k["high_price"],
        low=df_k["low_price"], close=df_k["close_price"],
        increasing=dict(
            line=dict(color=RED_UP, width=1.2),
            fillcolor=RED_UP,
        ),
        decreasing=dict(
            line=dict(color=GREEN_DN, width=1.2),
            fillcolor=GREEN_DN,
        ),
        whiskerwidth=0.5,
        name="", showlegend=False,
    ), row=1, col=1)

    fig.add_trace(go.Bar(
        x=x_values, y=df_k["volume"], marker_color=vol_color,
        name="", showlegend=False,
    ), row=2, col=1)

    # ── MA5 / MA10 / MA20 均线 ──
    for period, color, name in [
        (5, "#f59e0b", "MA5"),
        (10, "#a78bfa", "MA10"),
        (20, "#60a5fa", "MA20"),
    ]:
        if len(df_k) >= period:
            ma = df_k["close_price"].rolling(period).mean()
            fig.add_trace(go.Scatter(x=x_values, y=ma, mode="lines",
                line=dict(color=color, width=1.2), name=name), row=1, col=1)

    # ── 布局：同花顺/东方财富专业风格 ──
    fig.update_layout(
        height=560,
        margin=dict(t=8, b=8, l=0, r=20),
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
        xaxis_rangeslider_visible=False,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.00, xanchor="left", x=0,
                    bgcolor="rgba(255,255,255,0.85)", bordercolor="#d0d5dd", borderwidth=0.5),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1e293b", font_size=11, font_color="#ffffff",
                        bordercolor="#334155"),
        font=dict(family="-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif",
                  size=11, color="#475569"),
        # 十字光标（同花顺风格：实线细十字）
        xaxis=dict(type="category", categoryorder="array", categoryarray=list(x_values),
                   showspikes=True, spikemode="across", spikethickness=0.8,
                   spikecolor="#94a3b8", spikedash="solid"),
        yaxis=dict(showspikes=True, spikethickness=0.8,
                   spikecolor="#94a3b8", spikedash="solid"),
        bargap=0.1,  # 蜡烛紧凑，更接近专业密度
        dragmode="zoom",
    )

    # 主图 Y 轴：右侧价格标签，浅灰虚线网格
    y_min = low_price
    y_max = high_price
    pad = (y_max - y_min) * 0.06 if y_max > y_min else 10
    fig.update_yaxes(
        range=[y_min - pad, y_max + pad],
        showgrid=True, gridcolor="#e8ecf1", gridwidth=0.8, griddash="dot",
        tickformat=",.2f", tickfont=dict(size=11, color="#64748b", family="monospace"),
        side="right", row=1, col=1,
        zeroline=False,
        title_text="价格", title_font=dict(size=10, color="#94a3b8"),
    )
    fig.update_xaxes(showgrid=False, row=1, col=1)

    # 成交量副图
    fig.update_yaxes(
        showgrid=True, gridcolor="#e8ecf1", gridwidth=0.8, griddash="dot",
        tickfont=dict(size=9, color="#94a3b8"), side="right", row=2, col=1,
        zeroline=False,
    )
    fig.update_xaxes(
        showgrid=False, type="category",
        tickvals=x_values.iloc[::max(1, len(df_k)//6)],
        ticktext=df_k["x_label"].iloc[::max(1, len(df_k)//6)],
        tickfont=dict(size=10, color="#94a3b8"),
        row=2, col=1,
    )

    # 工具栏：极简，只保留基础功能
    config = {
        "displayModeBar": True,
        "modeBarButtonsToRemove": ["lasso2d", "select2d", "sendDataToCloud",
                                     "autoScale2d", "toggleSpikelines",
                                     "zoomIn2d", "zoomOut2d"],
        "modeBarButtonsToAdd": [],
        "displaylogo": False,
        "scrollZoom": True,
        "responsive": True,
    }
    st.markdown('<div class="chart-panel">', unsafe_allow_html=True)
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
    st.dataframe(disp[["round","开盘","最高","最低","收盘","涨跌幅","成交量"]].rename(columns={"round":"轮次"}), use_container_width=True, hide_index=True)

def page_admin_stock_summary():
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{esc(st.session_state.username)}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#eef2ff;margin-bottom:12px">股票汇总</div>""", unsafe_allow_html=True)
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
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{esc(st.session_state.username)}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#eef2ff;margin-bottom:12px">股票管理</div>""", unsafe_allow_html=True)
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
            # 预览自动计算的初始价
            preview_price = calc_initial_price(rev, ts_, ipe)
            st.caption(f"📌 初始价（自动计算）= {rev}×10000÷{ts_:.0f}÷{ipe} = **¥{preview_price}**")
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
        st.caption("Excel 五列：股票代码 / 公司名称 / 总股本(万股) / 初始净利润(万) / 行业PE")
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
    st.dataframe(sdf[["symbol", "name", "总股本", "净利润", "行业PE", "price", "碳排", "碳排均值", "幸福度", "lu"]].rename(
        columns={"symbol": "代码", "name": "名称", "price": "当前价", "lu": "更新"}), use_container_width=True, hide_index=True)
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
    st.caption("价格由撮合逻辑自动生成，不可手动修改。修改基础参数后系统自动计算理论价。")
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
                # 显示自动计算
                init_p = calc_initial_price(rev, ts_, ipe)
                st.info(f"📐 理论初始价 = {rev:,.0f}×10000÷{ts_:,.0f}÷{ipe} = **¥{init_p}**  ｜  当前市价 **¥{s['current_price']:.2f}**（由交易撮合决定）")
                if st.button("💾 保存参数", key=f"sv_{s['id']}", type="primary"):
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
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{esc(st.session_state.username)}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:14px;font-weight:600;color:#eef2ff;margin-bottom:12px">用户管理</div>""", unsafe_allow_html=True)
    users = get_all_users()
    df = pd.DataFrame(users)
    df["created_at"] = df["created_at"].apply(lambda x: str(x)[:19] if x else "-")
    df["状态"] = df.get("status", "active").fillna("active").map({"active": "正常", "disabled": "已禁用"})
    df.columns = ["ID", "用户名", "角色", "注册时间", "status_col", "状态"]
    df["角色"] = df["角色"].map({"admin": "管理员", "player": "选手"})
    st.markdown('<div class="desktop-table">', unsafe_allow_html=True)
    st.dataframe(df[["ID", "用户名", "角色", "状态", "注册时间"]], use_container_width=True, hide_index=True)
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
        st.dataframe(log_df, use_container_width=True, hide_index=True)
def page_admin_settle():
    st.markdown(f"""<div class="topbar"><span class="brand">双镜</span><span>{esc(st.session_state.username)}</span></div>""", unsafe_allow_html=True)
    st.markdown("""<div style="font-size:20px;font-weight:500;color:#eef2ff;margin-bottom:16px">市场控制</div>""", unsafe_allow_html=True)

    market_open = is_market_open()
    current_round = get_market_round()
    status = "交易中" if market_open else "已闭市"
    color = "#16a34a" if market_open else "#ef4444"

    # 初始化确认状态
    for k in ["cf_close", "cf_open", "cf_undo", "cf_r1"]:
        if k not in st.session_state: st.session_state[k] = False

    st.markdown(f"""
    <div style="background:rgba(10,20,42,.7);border:1px solid rgba(255,255,255,.04);border-radius:12px;padding:24px;text-align:center;margin-bottom:20px;">
        <div style="font-size:13px;color:#94a3b8;">当前市场状态</div>
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
        st.caption(f"当前数据库路径：{DB_PATH}")
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
PLAYER_NAV = ["总览", "交易大厅", "我的持仓", "交易记录", "K线展板"]
ADMIN_NAV = ["市场控制", "股票汇总", "股票管理", "用户管理", "K线展板"]

st.set_page_config(page_title="双镜 - 智能投资分析系统", layout="wide", initial_sidebar_state="auto")
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
        <div class="sb-brand"><div class="name">双镜</div><div class="sub">INSIGHT+</div></div>
        <div class="sb-user"><div class="uname">{esc(st.session_state.username)}</div><div class="urole"><span class="dot"></span>{role_text}{bal_text}</div></div>
        """, unsafe_allow_html=True)
        st.markdown('<div class="menu-group-label">导航</div>', unsafe_allow_html=True)
        sel = st.session_state.nav_current
        picked = st.radio("", nav, index=nav.index(sel) if sel in nav else 0, key="nav_radio", label_visibility="collapsed")
        picked = picked.strip()
        if picked != sel:
            st.session_state.nav_current = picked
            st.rerun()
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        if st.button("退出登录", key="sb_exit", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.username = ""
            st.session_state.role = ""
            st.rerun()
    st.markdown('<div class="mobile-only mobile-nav">', unsafe_allow_html=True)
    mob_pick = st.radio("", nav, index=nav.index(sel) if sel in nav else 0, key="nav_mobile", label_visibility="collapsed", horizontal=True)
    mob_new = mob_pick.split(" ", 1)[1] if " " in mob_pick else mob_pick
    if mob_new != st.session_state.nav_current:
        st.session_state.nav_current = mob_new
    st.markdown('</div>', unsafe_allow_html=True)
    sel = st.session_state.nav_current
    if sel in NAV: NAV[sel]()
    else: page_overview()

if __name__ == "__main__":
    main()
