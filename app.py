"""
碳交易模拟系统 — 完整版
基于Excel公式的股票/碳交易定价模型 + 专业K线图表 + 精美UI
"""
import os, sqlite3, hashlib, json
from datetime import datetime, timedelta
from functools import wraps

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ──────────────────────────────────────────────
# 数据库
# ──────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def hash_pwd(p): return hashlib.sha256(p.encode()).hexdigest()

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'player',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            current_price REAL DEFAULT 0,
            previous_close REAL DEFAULT 0,
            is_deleted INTEGER DEFAULT 0,
            total_shares REAL DEFAULT 10000,
            industry_pe REAL DEFAULT 20,
            carbon_price REAL DEFAULT 50,
            industry_carbon_mean REAL DEFAULT 50,
            premium_rate REAL DEFAULT 50,
            init_funds REAL DEFAULT 5000,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            stock_symbol TEXT NOT NULL,
            trade_type TEXT NOT NULL,
            price REAL NOT NULL,
            shares INTEGER NOT NULL,
            round INTEGER DEFAULT 0,
            trade_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS kline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_symbol TEXT NOT NULL,
            round INTEGER NOT NULL DEFAULT 0,
            open_price REAL DEFAULT 0,
            high_price REAL DEFAULT 0,
            low_price REAL DEFAULT 0,
            close_price REAL DEFAULT 0,
            volume REAL DEFAULT 0,
            buy_total REAL DEFAULT 0,
            sell_total REAL DEFAULT 0,
            change_pct REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS rounds (
            stock_symbol TEXT NOT NULL,
            round INTEGER NOT NULL DEFAULT 0,
            is_settled INTEGER DEFAULT 0,
            PRIMARY KEY (stock_symbol, round)
        );
    """)
    conn.commit()
    if cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        _seed(conn)
    # Ensure each stock has a round 0
    for s in cur.execute("SELECT symbol FROM stocks WHERE is_deleted=0").fetchall():
        cur.execute("INSERT OR IGNORE INTO rounds (stock_symbol,round,is_settled) VALUES (?,1,1)", (s["symbol"],))
    conn.commit()
    conn.close()

def _seed(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO users VALUES (1,'admin',?,'admin',datetime())", (hash_pwd("admin123"),))
    for i,u in enumerate(["player1","player2","player3"],2):
        cur.execute(f"INSERT INTO users VALUES ({i},?,?,'player',datetime())", (u,hash_pwd(u)))
    stocks = [
        ("TSLA","特斯拉",250.0,5000), ("AAPL","苹果",175.0,3500), ("NVDA","英伟达",450.0,9000)
    ]
    for sym,name,price,funds in stocks:
        cur.execute("""INSERT INTO stocks(symbol,name,current_price,previous_close,init_funds)
                       VALUES (?,?,?,?,?)""", (sym,name,price,price,funds))
        cur.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES (?,1,1)", (sym,))
    trades = [
        ("player1","TSLA","buy",200.0,100,1), ("player1","AAPL","buy",150.0,50,1),
        ("player1","TSLA","sell",240.0,80,1), ("player2","NVDA","buy",400.0,30,1),
        ("player2","AAPL","sell",160.0,40,1), ("player3","TSLA","buy",210.0,50,1),
        ("player3","NVDA","buy",420.0,20,1),
    ]
    for args in trades:
        cur.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,?,?,?,?)", args)
    conn.commit()

# ──────────────────────────────────────────────
# 价格计算引擎（Excel公式）
# ──────────────────────────────────────────────
def compute_price(stock):
    """
    理论价 = 昨收价 × 买入总额/卖出总额 × 溢价因子 × 碳因子
    当日价 = clamp(理论价, 昨收价×0.9, 昨收价×1.1)
    溢价因子 = 1 + 0.2 × (溢价率-50)/50
    碳因子 = 1 - 0.5 × (碳价-碳均值)/碳均值
    """
    prev_close = stock.get("previous_close", stock.get("current_price", 50)) or 50
    buy_total = max(stock.get("buy_total", 0), 1)
    sell_total = max(stock.get("sell_total", 0), 1)

    premium_factor = 1 + 0.2 * (stock.get("premium_rate", 50) - 50) / 50
    carbon_mean = max(stock.get("industry_carbon_mean", 50), 1)
    carbon_factor = 1 - 0.5 * (stock.get("carbon_price", 50) - carbon_mean) / carbon_mean

    theoretical = prev_close * (buy_total / sell_total) * premium_factor * carbon_factor
    daily_max = round(prev_close * 1.1, 2)
    daily_min = round(prev_close * 0.9, 2)
    return max(daily_min, min(daily_max, round(theoretical, 2)))

# ──────────────────────────────────────────────
# 结算引擎
# ──────────────────────────────────────────────
def settle_round(symbol):
    """结算当前轮次：计算价格 → 生成K线 → 开启新一轮"""
    conn = get_db(); cur = conn.cursor()
    stock = dict(cur.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone())
    row = cur.execute("SELECT MAX(round) as r FROM rounds WHERE stock_symbol=?", (symbol,)).fetchone()
    current_round = row["r"] if row and row["r"] else 0

    # 获取本轮交易数据
    txns = cur.execute(
        "SELECT trade_type, price, shares FROM transactions WHERE stock_symbol=? AND round=?",
        (symbol, current_round)
    ).fetchall()

    buy_total = sum(t["price"] * t["shares"] for t in txns if t["trade_type"] == "buy")
    sell_total = sum(t["price"] * t["shares"] for t in txns if t["trade_type"] == "sell")
    total_volume = sum(t["shares"] for t in txns)

    # 计算新价格
    price_params = dict(stock, buy_total=buy_total, sell_total=sell_total)
    new_price = compute_price(price_params)

    # 生成K线
    prev_close = stock["previous_close"] or stock["current_price"]
    change_pct = round((new_price - prev_close) / prev_close * 100, 2) if prev_close else 0
    high = max(new_price, prev_close)
    low = min(new_price, prev_close)

    cur.execute("""INSERT INTO kline(stock_symbol,round,open_price,high_price,low_price,close_price,
                 volume,buy_total,sell_total,change_pct) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (symbol, current_round, prev_close, high, low, new_price,
                 total_volume, buy_total, sell_total, change_pct))

    # 更新股票价格
    new_round = current_round + 1
    cur.execute("UPDATE stocks SET previous_close=?, current_price=? WHERE symbol=?",
                (new_price, new_price, symbol))
    cur.execute("UPDATE rounds SET is_settled=1 WHERE stock_symbol=? AND round=?",
                (symbol, current_round))
    cur.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES (?,?,0)",
                (symbol, new_round))
    conn.commit(); conn.close()
    return new_price

# ──────────────────────────────────────────────
# 用户操作
# ──────────────────────────────────────────────
def auth_user(u, p):
    conn = get_db()
    r = conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
    conn.close()
    if r and r["password"] == hash_pwd(p): return True, r["role"]
    return False, ""

def register_user(u, p, role="player"):
    conn = get_db()
    try:
        conn.execute("INSERT INTO users(username,password,role) VALUES(?,?,?)", (u, hash_pwd(p), role))
        conn.commit()
        return True, "注册成功"
    except sqlite3.IntegrityError: return False, "用户名已存在"
    finally: conn.close()

def get_all_users():
    conn = get_db()
    r = conn.execute("SELECT id,username,role,created_at FROM users ORDER BY id").fetchall()
    conn.close(); return [dict(x) for x in r]

def reset_pwd(u, np):
    conn = get_db()
    conn.execute("UPDATE users SET password=? WHERE username=?", (hash_pwd(np), u))
    conn.commit(); conn.close()

# ──────────────────────────────────────────────
# 股票操作
# ──────────────────────────────────────────────
def get_stocks():
    conn = get_db()
    r = conn.execute("SELECT * FROM stocks WHERE is_deleted=0 ORDER BY symbol").fetchall()
    conn.close(); return [dict(x) for x in r]

def get_stock(sid):
    conn = get_db()
    r = conn.execute("SELECT * FROM stocks WHERE id=?", (sid,)).fetchone()
    conn.close(); return dict(r) if r else None

def add_stock(sym, name, price):
    conn = get_db()
    try:
        funds = price * 10000 * 20 / 10000  # 反算初始资金
        conn.execute("""INSERT INTO stocks(symbol,name,current_price,previous_close,init_funds)
                        VALUES(?,?,?,?,?)""", (sym.upper(), name, price, price, funds))
        sym_u = sym.upper()
        conn.execute("INSERT OR IGNORE INTO rounds(stock_symbol,round,is_settled) VALUES (?,1,1)", (sym_u,))
        conn.commit(); return True, "添加成功"
    except sqlite3.IntegrityError: return False, "股票代码已存在"
    finally: conn.close()

def update_stock_params(sid, **kw):
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in kw)
    vals = list(kw.values()) + [sid]
    conn.execute(f"UPDATE stocks SET {sets} WHERE id=?", vals)
    conn.commit(); conn.close()

def delete_stock(sid):
    conn = get_db()
    conn.execute("UPDATE stocks SET is_deleted=1 WHERE id=?", (sid,))
    conn.commit(); conn.close()

# ──────────────────────────────────────────────
# 交易 & 持仓
# ──────────────────────────────────────────────
def add_trade(username, symbol, trade_type, price, shares):
    conn = get_db()
    row = conn.execute("SELECT MAX(round) as r FROM rounds WHERE stock_symbol=? AND is_settled=0",
                       (symbol,)).fetchone()
    current_round = row["r"] if row and row["r"] else 1
    conn.execute("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,?,?,?,?)",
                 (username, symbol, trade_type, price, shares, current_round))
    conn.commit(); conn.close()

def get_user_portfolio(username):
    """净持仓计算"""
    conn = get_db()
    buys = conn.execute(
        "SELECT stock_symbol, SUM(shares) AS s, SUM(price*shares) AS c FROM transactions WHERE username=? AND trade_type='buy' GROUP BY stock_symbol",
        (username,)).fetchall()
    sells = conn.execute(
        "SELECT stock_symbol, SUM(shares) AS s FROM transactions WHERE username=? AND trade_type IN ('sell','force_close') GROUP BY stock_symbol",
        (username,)).fetchall()
    conn.close()
    sell_map = {r["stock_symbol"]: r["s"] for r in sells}
    stocks = {s["symbol"]: s for s in get_stocks()}
    rows = []
    for b in buys:
        sym = b["stock_symbol"]
        net = b["s"] - sell_map.get(sym, 0)
        if net <= 0: continue
        avg = round(b["c"] / b["s"], 2)
        info = stocks.get(sym, {"name":sym, "current_price":avg})
        cp = info.get("current_price", avg)
        mv = round(cp * net, 2)
        pnl = round((cp - avg) * net, 2)
        pr = round((cp-avg)/avg*100, 2) if avg else 0
        rows.append({"symbol":sym,"name":info["name"],"shares":int(net),"avg_cost":avg,
                     "current_price":cp,"market_value":mv,"pnl":pnl,"pnl_ratio":pr})
    return pd.DataFrame(rows)

def get_user_market_making(username):
    conn = get_db()
    rows = conn.execute("""SELECT t.stock_symbol,t.price AS sp,t.shares,t.trade_date,
        COALESCE(s.current_price,t.price) AS cp,COALESCE(s.name,t.stock_symbol) AS nm
        FROM transactions t LEFT JOIN stocks s ON t.stock_symbol=s.symbol
        WHERE t.username=? AND t.trade_type='sell' ORDER BY t.trade_date DESC""", (username,)).fetchall()
    conn.close()
    return pd.DataFrame([{"股票":r["nm"],"卖出价":r["sp"],"当前价":r["cp"],
        "数量":r["shares"],"对手方盈亏":round((r["cp"]-r["sp"])*r["shares"],2),
        "时间":r["trade_date"]} for r in rows])

def get_user_overview(username):
    pf = get_user_portfolio(username)
    if pf.empty: return {"total_assets":0,"total_cost":0,"total_pnl":0,"pnl_ratio":0,"stock_count":0,"stock_pnl":[]}
    ta, tc = pf["market_value"].sum(), (pf["avg_cost"]*pf["shares"]).sum()
    tp = ta - tc
    return {"total_assets":round(ta,2),"total_cost":round(tc,2),"total_pnl":round(tp,2),
            "pnl_ratio":round(tp/tc*100,2) if tc else 0,"stock_count":len(pf),
            "stock_pnl":pf[["name","symbol","pnl"]].to_dict("records")}

# ──────────────────────────────────────────────
# 管理员汇总
# ──────────────────────────────────────────────
def get_admin_summary():
    stocks = get_stocks()
    if not stocks: return pd.DataFrame()
    conn = get_db()
    players = conn.execute("SELECT username FROM users WHERE role='player'").fetchall()
    conn.close()
    all_pfs = {}
    for p in players:
        df = get_user_portfolio(p["username"])
        if not df.empty: all_pfs[p["username"]] = df
    rows = []
    for s in stocks:
        sym = s["symbol"]; ts = tc = tp = 0.0; cnt = 0
        for un, pf in all_pfs.items():
            r = pf[pf["symbol"]==sym]
            if r.empty: continue
            rr = r.iloc[0]; ts += rr["shares"]; tc += rr["avg_cost"]*rr["shares"]; tp += rr["pnl"]; cnt += 1
        pct = round(tp/tc*100,2) if cnt and tc else 0
        rows.append({"股票名称":s["name"],"代码":sym,"当前价":s["current_price"],
            "持有用户数":cnt,"总持仓量":int(ts),"总成本":round(tc,2),"总盈亏":round(tp,2),"收益率":pct})
    return pd.DataFrame(rows)

def get_holder_detail(symbol):
    conn = get_db()
    players = conn.execute("SELECT username FROM users WHERE role='player'").fetchall()
    conn.close()
    r = []
    for p in players:
        pf = get_user_portfolio(p["username"])
        if pf.empty: continue
        h = pf[pf["symbol"]==symbol]
        if h.empty: continue
        rr = h.iloc[0]
        r.append({"用户名":p["username"],"持仓量":int(rr["shares"]),"成本价":rr["avg_cost"],
                  "当前价":rr["current_price"],"盈亏":rr["pnl"],"收益率":rr["pnl_ratio"]})
    return pd.DataFrame(r)

def get_kline_data(symbol):
    conn = get_db()
    r = conn.execute("SELECT * FROM kline WHERE stock_symbol=? ORDER BY round", (symbol,)).fetchall()
    conn.close()
    return [dict(x) for x in r]

def get_platform_stats():
    s = get_admin_summary()
    if s.empty: return {"total_mv":0,"total_pnl":0,"active_users":0}
    conn = get_db()
    cnt = conn.execute("SELECT COUNT(*) FROM users WHERE role='player'").fetchone()[0]
    conn.close()
    return {"total_mv":round((s["当前价"]*s["总持仓量"]).sum(),2),
            "total_pnl":round(s["总盈亏"].sum(),2),"active_users":cnt}

# ──────────────────────────────────────────────
# 华丽 UI 主题
# ──────────────────────────────────────────────
CUSTOM_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', -apple-system, sans-serif; }
    .stApp { background: #f0f4f8; }

    /* ─── 登录页 AI 主题 ─── */
    .ai-login-wrap {
        position: fixed; top:0; left:0; right:0; bottom:0; z-index:-1;
        background: linear-gradient(135deg, #0a0e1a 0%, #1a1040 30%, #0d2137 60%, #0a1628 100%);
        overflow: hidden;
    }
    .ai-login-wrap::before {
        content:''; position:absolute; width:600px; height:600px; border-radius:50%;
        background: radial-gradient(circle, rgba(100,50,255,.15) 0%, transparent 70%);
        top:-150px; right:-100px; animation: floatOrb 12s ease-in-out infinite;
    }
    .ai-login-wrap::after {
        content:''; position:absolute; width:500px; height:500px; border-radius:50%;
        background: radial-gradient(circle, rgba(0,200,255,.1) 0%, transparent 70%);
        bottom:-100px; left:-100px; animation: floatOrb 15s ease-in-out infinite reverse;
    }
    @keyframes floatOrb {
        0%,100%{transform:translate(0,0) scale(1)} 50%{transform:translate(40px,-30px) scale(1.1)}
    }
    /* 网格线 */
    .ai-grid {
        position:fixed; top:0; left:0; right:0; bottom:0; z-index:-1;
        background-image:
            linear-gradient(rgba(255,255,255,.03) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,.03) 1px, transparent 1px);
        background-size: 60px 60px;
    }
    /* 粒子 */
    .ai-particles { position:fixed; top:0; left:0; right:0; bottom:0; z-index:-1; pointer-events:none; }
    .particle {
        position:absolute; width:3px; height:3px; background:rgba(100,200,255,.5);
        border-radius:50%; animation: particleFloat 20s infinite;
    }
    .particle:nth-child(1) { left:10%; animation-delay:0s; }
    .particle:nth-child(2) { left:20%; animation-delay:2s; width:2px; }
    .particle:nth-child(3) { left:35%; animation-delay:4s; height:2px; }
    .particle:nth-child(4) { left:50%; animation-delay:6s; width:4px; height:4px; background:rgba(200,100,255,.4); }
    .particle:nth-child(5) { left:65%; animation-delay:8s; }
    .particle:nth-child(6) { left:75%; animation-delay:10s; width:2px; }
    .particle:nth-child(7) { left:88%; animation-delay:12s; height:2px; }
    .particle:nth-child(8) { left:45%; animation-delay:14s; width:3px; background:rgba(0,200,200,.3); }
    @keyframes particleFloat {
        0%{top:110%;opacity:0;transform:scale(0)} 10%{opacity:1}
        90%{opacity:1} 100%{top:-10%;opacity:0;transform:scale(1.5)}
    }
    /* 登录卡片 */
    .ai-login-card {
        background: rgba(255,255,255,.04); backdrop-filter: blur(24px);
        -webkit-backdrop-filter: blur(24px);
        border: 1px solid rgba(255,255,255,.08);
        border-radius: 24px; padding: 2.8rem 2.5rem;
        box-shadow: 0 20px 60px rgba(0,0,0,.5), inset 0 1px 0 rgba(255,255,255,.08);
        position:relative; overflow:hidden;
    }
    .ai-login-card::before {
        content:''; position:absolute; top:0; left:0; right:0; height:2px;
        background: linear-gradient(90deg, transparent, #6366f1, #06b6d4, transparent);
    }
    .ai-logo {
        text-align:center; margin-bottom:2rem;
    }
    .ai-logo .icon {
        font-size:3.2rem; display:block; margin-bottom:6px;
    }
    .ai-logo h1 {
        font-size:1.6rem; font-weight:800; color:#fff; margin:0;
        letter-spacing:-.5px; background: linear-gradient(135deg, #a78bfa, #06b6d4);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    .ai-logo p {
        font-size:.8rem; color:rgba(255,255,255,.4); margin:4px 0 0 0;
        letter-spacing:2px; text-transform:uppercase; font-weight:600;
    }
    .ai-logo .tagline {
        font-size:.75rem; color:rgba(255,255,255,.25); margin-top:8px;
        letter-spacing:3px; text-transform:uppercase;
    }
    .ai-login-card .stTextInput input {
        background: rgba(255,255,255,.06) !important;
        border: 1px solid rgba(255,255,255,.1) !important;
        border-radius: 12px !important; color: #fff !important;
        padding: 12px 16px !important; font-size: .9rem !important;
        transition: all .2s;
    }
    .ai-login-card .stTextInput input:focus {
        border-color: #6366f1 !important;
        box-shadow: 0 0 20px rgba(99,102,241,.15) !important;
    }
    .ai-login-card .stTextInput input::placeholder { color: rgba(255,255,255,.25) !important; }
    .ai-login-card .stButton button[kind="primary"] {
        background: linear-gradient(135deg, #6366f1, #06b6d4) !important;
        border: none !important; border-radius: 12px !important;
        padding: 10px !important; font-weight: 700 !important;
        font-size: .95rem !important; letter-spacing: .5px;
        transition: all .2s !important;
    }
    .ai-login-card .stButton button[kind="primary"]:hover {
        transform: translateY(-1px); box-shadow: 0 8px 25px rgba(99,102,241,.35) !important;
    }
    .ai-login-card .stTabs { margin-top: .5rem; }
    .ai-login-card .stTabs button {
        color: rgba(255,255,255,.4) !important; font-weight:600 !important;
        letter-spacing:.5px; font-size:.8rem !important;
    }
    .ai-login-card .stTabs button[aria-selected="true"] {
        color: #a78bfa !important;
    }
    .ai-login-card .stTabs [data-baseweb="tab-highlight"] {
        background: linear-gradient(90deg, #6366f1, #06b6d4) !important;
    }
    .ai-login-card label { color: rgba(255,255,255,.5) !important; font-size:.75rem !important; }
    .ai-login-card .stAlert { border-radius: 12px; font-size:.8rem; }
    .ai-login-card .stAlert [data-testid="stAlertContainer"] { border-radius: 12px; }
    .ai-footer {
        text-align:center; margin-top:1.5rem;
        color:rgba(255,255,255,.15); font-size:.7rem; letter-spacing:1px;
    }

    /* ─── 主应用样式 ─── */
    .main-header {
        background: linear-gradient(135deg, #0f1729 0%, #1a2a5e 100%);
        padding: 2rem 2rem; border-radius: 0 0 24px 24px;
        margin: -3rem -3rem 2rem -3rem; text-align: center;
    }
    .main-header h1 { color: #fff; font-size: 2.2rem; font-weight: 700; letter-spacing: 1px; margin:0; }
    .main-header p { color: rgba(255,255,255,.6); font-size:.95rem; margin:4px 0 0 0; }
    .card {
        background: #fff; border-radius: 14px; padding: 1.2rem 1.5rem;
        box-shadow: 0 2px 12px rgba(0,0,0,.06); border:1px solid rgba(0,0,0,.04);
        margin-bottom:1rem; transition: transform .15s;
    }
    .card:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,0,0,.1); }
    .card-title { font-size:.85rem; color:#8892a4; font-weight:600; text-transform:uppercase; letter-spacing:.5px; }
    .card-value { font-size:1.8rem; font-weight:700; color:#0f1729; margin:4px 0; }
    .card-sub { font-size:.8rem; color:#555; }
    .up { color:#00c853!important; } .down { color:#ff1744!important; }
    section[data-testid="stSidebar"] > div:first-child { background: linear-gradient(180deg, #0f1729 0%, #152040 100%); }
    section[data-testid="stSidebar"] .stMarkdown { color: rgba(255,255,255,.85); }
    section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.1); }
    section[data-testid="stSidebar"] .stButton button { background: rgba(255,255,255,.06); border-radius: 10px; color:#fff; font-weight:600; }
    section[data-testid="stSidebar"] .stButton button:hover { background: rgba(255,255,255,.12); }
    .stButton button[kind="primary"] { background: linear-gradient(135deg, #1a2a5e, #2a4a8e); border: none; border-radius: 8px; font-weight:600; }
    .stButton button[kind="primary"]:hover { background: linear-gradient(135deg, #253a7a, #3a5aae); }
    .stTextInput input { border-radius: 8px; border: 1px solid #d0d5dd; }
    div[data-testid="stMetricValue"] { font-size: 1.6rem!important; font-weight: 700!important; }
    div[data-testid="stMetricDelta"] { font-size: .85rem!important; }
    .kpi-box {
        background:#fff; border-radius:16px; padding:1.2rem; text-align:center;
        box-shadow:0 2px 10px rgba(0,0,0,.05); border:1px solid rgba(0,0,0,.04);
    }
    .kpi-box .label { font-size:.8rem; color:#8892a4; text-transform:uppercase; letter-spacing:.5px; }
    .kpi-box .value { font-size:1.8rem; font-weight:700; color:#0f1729; margin:4px 0; }
    .kpi-box .delta { font-size:.85rem; }
    div[data-testid="stDataFrame"] { font-size:.8rem; }
    .stDataFrame [data-testid="StyledDataFrameDataCell"] { font-size:.8rem; }
</style>
"""

def render_header():
    """深蓝渐变页面头"""
    role_tag = "🛡️ 管理员" if st.session_state.role == "admin" else "🎯 选手"
    st.markdown(f"""
    <div class="main-header">
        <h1>📊 碳交易模拟系统</h1>
        <p>{st.session_state.username} · {role_tag}</p>
    </div>
    """, unsafe_allow_html=True)

def metric_card(title, value, delta=None, delta_color="normal"):
    dc = {"normal":"up","inverse":"down","off":"gray"}.get(delta_color,"gray")
    d = f'<div class="delta {dc}">{delta}</div>' if delta else ""
    return f'<div class="kpi-box"><div class="label">{title}</div><div class="value">{value}</div>{d}</div>'

# ──────────────────────────────────────────────
# 页面
# ──────────────────────────────────────────────

def page_login():
    # AI 主题背景
    st.markdown("""
    <div class="ai-login-wrap"></div>
    <div class="ai-grid"></div>
    <div class="ai-particles">
        <div class="particle"></div><div class="particle"></div><div class="particle"></div>
        <div class="particle"></div><div class="particle"></div><div class="particle"></div>
        <div class="particle"></div><div class="particle"></div>
    </div>
    """, unsafe_allow_html=True)

    _, mid, _ = st.columns([1, 2.4, 1])
    with mid:
        st.markdown("<div style='height:12vh'></div>", unsafe_allow_html=True)
        st.markdown("""
        <div class="ai-login-card">
            <div class="ai-logo">
                <span class="icon">🧠</span>
                <h1>AI 碳交易模拟系统</h1>
                <p>AI-Powered Carbon Trading Simulator</p>
                <div class="tagline">✦ 商业竞赛 · 多用户仿真平台 ✦</div>
            </div>
        """, unsafe_allow_html=True)

        tab1, tab2 = st.tabs(["🔐 登录", "📝 注册"])

        with tab1:
            with st.form("login"):
                u = st.text_input("", placeholder="👤 用户名", label_visibility="collapsed")
                p = st.text_input("", type="password", placeholder="🔑 密码", label_visibility="collapsed")
                if st.form_submit_button("⚡ 进入系统", type="primary", use_container_width=True):
                    if not u or not p:
                        st.error("请填写用户名和密码")
                    else:
                        ok, role = auth_user(u, p)
                        if ok:
                            st.session_state.logged_in = True
                            st.session_state.username = u
                            st.session_state.role = role
                            st.rerun()
                        else:
                            st.error("用户名或密码错误")

        with tab2:
            with st.form("register"):
                st.markdown("<p style='color:rgba(255,255,255,.4);font-size:.8rem;margin:0 0 8px 0'>创建新账户后即可开始模拟交易</p>", unsafe_allow_html=True)
                u2 = st.text_input(" ", placeholder="👤 用户名（至少3位）", label_visibility="collapsed", key="ru")
                p2 = st.text_input("  ", type="password", placeholder="🔑 密码（至少4位）", label_visibility="collapsed", key="rp")
                p3 = st.text_input("   ", type="password", placeholder="🔑 确认密码", label_visibility="collapsed", key="rp2")
                if st.form_submit_button("📝 注册", type="primary", use_container_width=True):
                    if not u2 or not p2:
                        st.error("请填写完整")
                    elif len(u2) < 3:
                        st.error("用户名至少3位")
                    elif len(p2) < 4:
                        st.error("密码至少4位")
                    elif p2 != p3:
                        st.error("两次密码不一致")
                    else:
                        ok, m = register_user(u2, p2)
                        st.success(m) if ok else st.error(m)

        st.markdown("""
            <div class="ai-footer">AI Trading System v2.0 · Powered by Intelligent Algorithms</div>
        </div>
        """, unsafe_allow_html=True)

def page_overview():
    render_header()
    if st.session_state.role == "admin":
        stats = get_platform_stats()
        cols = st.columns(3)
        for i,(t,v) in enumerate([("🏦 总市值",f"¥{stats['total_mv']:,.0f}"),
                                   ("📈 平台总盈亏",f"¥{stats['total_pnl']:+,.0f}"),
                                   ("👥 活跃用户",str(stats['active_users']))]):
            cols[i].markdown(metric_card(t,v), unsafe_allow_html=True)
        st.divider()
        summary = get_admin_summary()
        if not summary.empty:
            sdf = summary.sort_values("总盈亏")
            fig = go.Figure(go.Bar(x=sdf["股票名称"], y=sdf["总盈亏"],
                text=sdf["总盈亏"].apply(lambda x: f"¥{x:+,.0f}"),
                marker_color=["#ff1744" if v<0 else "#00c853" for v in sdf["总盈亏"]]))
            fig.update_traces(textposition="outside")
            fig.update_layout(title={"text":"🏆 各股票盈亏排行","x":0.5},
                xaxis_title="",yaxis_title="总盈亏(¥)",height=380,
                plot_bgcolor="rgba(0,0,0,0)",paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, use_container_width=True)
        return

    data = get_user_overview(st.session_state.username)
    cols = st.columns(4)
    for i,(t,v,d,c) in enumerate([
        ("💰 总资产",f"¥{data['total_assets']:,.0f}",None,"off"),
        ("📉 总成本",f"¥{data['total_cost']:,.0f}",None,"off"),
        ("📈 总盈亏",f"¥{data['total_pnl']:+,.0f}",f"{data['pnl_ratio']:+.2f}%",
         "normal" if data['total_pnl']>=0 else "inverse"),
        ("🧾 持仓数",str(data['stock_count']),None,"off"),
    ]):
        cols[i].markdown(metric_card(t,v,d,c), unsafe_allow_html=True)

    if data["stock_pnl"]:
        st.divider()
        df = pd.DataFrame(data["stock_pnl"])
        fig = go.Figure(go.Bar(x=df["name"], y=df["pnl"],
            text=df["pnl"].apply(lambda x: f"¥{x:+,.0f}"),
            marker_color=["#00c853" if v>=0 else "#ff1744" for v in df["pnl"]]))
        fig.update_traces(textposition="outside")
        fig.update_layout(title={"text":"📊 各股票盈亏","x":0.5},
            xaxis_title="",yaxis_title="盈亏(¥)",height=350,
            plot_bgcolor="rgba(0,0,0,0)",paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("💡 暂无持仓，请在「交易大厅」买入股票")

def page_trade_hall():
    render_header()
    stocks = get_stocks()
    if not stocks: st.error("暂无可用股票"); return
    opts = {f"{s['name']} ({s['symbol']}) — ¥{s['current_price']:,.2f}":s for s in stocks}

    # 当前轮次信息
    conn = get_db()
    rr = {}
    for s in stocks:
        r = conn.execute("SELECT MAX(round) as r, is_settled FROM rounds WHERE stock_symbol=?",
                         (s["symbol"],)).fetchone()
        rr[s["symbol"]] = r
    conn.close()

    col_left, col_right = st.columns([1,1.2])
    with col_left:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### 📝 下单")
        with st.form("trade_form"):
            sel = st.selectbox("选择股票", list(opts.keys()))
            stock = opts[sel]
            direction = st.radio("方向", ["📈 买入", "📉 卖出"], horizontal=True)
            c1,c2 = st.columns(2)
            with c1: price = st.number_input("委托价(元)", min_value=0.01, value=float(stock["current_price"]), step=0.5, format="%.2f")
            with c2: shares = st.number_input("数量(股)", min_value=1, step=100, format="%d")
            if st.form_submit_button("⚡ 提交委托", type="primary", use_container_width=True):
                tt = "buy" if "买入" in direction else "sell"
                add_trade(st.session_state.username, stock["symbol"], tt, price, shares)
                st.success(f"✅ 委托已提交：{'买入' if tt=='buy' else '卖出'} {shares}股 × ¥{price:.2f}")
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

        # 价格参数展示
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### 📐 定价参数")
        sel_sym = st.selectbox("查看参数", [f"{s['name']}({s['symbol']})" for s in stocks], key="param_sel")
        sym = sel_sym.split("(")[1].rstrip(")")
        s = next(s for s in stocks if s["symbol"]==sym)
        c1,c2 = st.columns(2)
        c1.metric("昨收价", f"¥{s['previous_close']:.2f}")
        c2.metric("当前价", f"¥{s['current_price']:.2f}")
        c1.metric("溢价率", f"{s['premium_rate']}%")
        c2.metric("碳价", f"¥{s['carbon_price']}")
        st.markdown('</div>', unsafe_allow_html=True)

    with col_right:
        # 当前未结算轮次的交易
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### 📋 本轮委托记录")
        sym2 = st.selectbox("筛选股票", [f"{s['name']}({s['symbol']})" for s in stocks], key="txn_sym")
        sym2 = sym2.split("(")[1].rstrip(")")
        this_round = rr.get(sym2, {}).get("r", 1) if isinstance(rr.get(sym2), dict) else 1
        conn = get_db()
        txns = conn.execute(
            "SELECT username,trade_type,price,shares,trade_date FROM transactions WHERE stock_symbol=? AND round=? ORDER BY trade_date DESC",
            (sym2, this_round)
        ).fetchall()
        conn.close()
        if txns:
            d = pd.DataFrame([dict(x) for x in txns])
            d["方向"] = d["trade_type"].map({"buy":"📈 买入","sell":"📉 卖出","force_close":"⚠️ 平仓"})
            d["价格"] = d["price"].apply(lambda x: f"¥{x:,.2f}")
            d["金额"] = (d["price"]*d["shares"]).apply(lambda x: f"¥{x:,.0f}")
            st.dataframe(d[["username","方向","价格","shares","金额"]].rename(
                columns={"username":"用户","shares":"数量"}), use_container_width=True, hide_index=True)
        else:
            st.info("本轮暂无委托")
        st.markdown('</div>', unsafe_allow_html=True)

def page_portfolio():
    render_header()
    pf = get_user_portfolio(st.session_state.username)
    if pf.empty: st.info("💡 暂无持仓，请在「交易大厅」买入股票"); return
    d = pf[["name","shares","avg_cost","current_price","market_value","pnl","pnl_ratio"]].copy()
    d.columns = ["股票","持仓","成本价","现价","市值","盈亏","收益率"]
    d["成本价"]=d["成本价"].apply(lambda x:f"¥{x:,.2f}")
    d["现价"]=d["现价"].apply(lambda x:f"¥{x:,.2f}")
    d["市值"]=d["市值"].apply(lambda x:f"¥{x:,.2f}")
    d["盈亏"]=d["盈亏"].apply(lambda x:f"¥{x:+,.2f}")
    d["收益率"]=d["收益率"].apply(lambda x:f"{x:+.2f}%")
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.dataframe(d, use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)

    tv = pf["market_value"].sum()
    tc = (pf["avg_cost"]*pf["shares"]).sum()
    tp = tv - tc
    cols = st.columns(3)
    cols[0].markdown(metric_card("总市值",f"¥{tv:,.0f}"), unsafe_allow_html=True)
    cols[1].markdown(metric_card("总成本",f"¥{tc:,.0f}"), unsafe_allow_html=True)
    dc = "normal" if tp>=0 else "inverse"
    cols[2].markdown(metric_card("总盈亏",f"¥{tp:+,.0f}",f"{tp/tc*100:+.2f}%" if tc else "0%",dc), unsafe_allow_html=True)

def page_market_making():
    render_header()
    mm = get_user_market_making(st.session_state.username)
    tab1, tab2 = st.tabs(["📤 做市卖出", "⚠️ 强制平仓"])
    with tab1:
        if mm.empty: st.info("尚无做市记录")
        else:
            d = mm.copy()
            d["卖出价"]=d["卖出价"].apply(lambda x:f"¥{x:,.2f}")
            d["当前价"]=d["当前价"].apply(lambda x:f"¥{x:,.2f}")
            d["对手方盈亏"]=d["对手方盈亏"].apply(lambda x:f"¥{x:+,.2f}")
            st.dataframe(d, use_container_width=True, hide_index=True)
            st.metric("客户总盈亏", f"¥{mm['对手方盈亏'].sum():+,.2f}")
    with tab2:
        st.info("暂无平仓记录")

def page_kline():
    render_header()
    stocks = get_stocks()
    if not stocks: st.info("暂无数据"); return
    opts = {f"{s['name']} ({s['symbol']})":s for s in stocks}
    sel = st.selectbox("选择股票查看K线", list(opts.keys()))
    sym = opts[sel]["symbol"]
    s = opts[sel]

    data = get_kline_data(sym)
    if not data:
        st.info("💡 尚无K线数据，请管理员在「交易管理」中结算轮次")
        # 显示模拟K线（基于价格模拟）
        st.info("正在生成模拟走势…")
        import numpy as np
        base = s["current_price"]
        np.random.seed(42)
        closes = [base]
        for i in range(49):
            change = np.random.normal(0, base*0.025)
            nc = max(base*0.5, min(base*1.5, closes[-1]+change))
            closes.append(nc)
            base_use = closes[-1]
        data = []
        for i in range(1, len(closes)):
            o = closes[i-1]
            c = closes[i]
            h = max(o,c)*(1+abs(np.random.normal(0,.01)))
            l = min(o,c)*(1-abs(np.random.normal(0,.01)))
            v = abs(int(np.random.normal(5000,2000)))
            data.append({"round":i,"open_price":round(o,2),"high_price":round(h,2),
                        "low_price":round(l,2),"close_price":round(c,2),"volume":v,
                        "change_pct":round((c-o)/o*100,2)})

    df_k = pd.DataFrame(data)
    if df_k.empty: return

    # 专业蜡烛图
    colors = ["#00c853" if r["close_price"]>=r["open_price"] else "#ff1744" for _,r in df_k.iterrows()]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.05, row_heights=[0.75,0.25],
        subplot_titles=(f"{sel} — 日K线图", "成交量"))

    fig.add_trace(go.Candlestick(
        x=df_k.index, open=df_k["open_price"], high=df_k["high_price"],
        low=df_k["low_price"], close=df_k["close_price"],
        increasing_line_color="#00c853", decreasing_line_color="#ff1744",
        name="K线"), row=1, col=1)

    fig.add_trace(go.Bar(x=df_k.index, y=df_k["volume"],
        marker_color=colors, name="成交量", showlegend=False), row=2, col=1)

    # 均线
    if len(df_k)>=5:
        ma5 = df_k["close_price"].rolling(5).mean()
        fig.add_trace(go.Scatter(x=df_k.index, y=ma5, line=dict(color="#ff9100",width=1.5),
            name="MA5",showlegend=False), row=1, col=1)
    if len(df_k)>=20:
        ma20 = df_k["close_price"].rolling(20).mean()
        fig.add_trace(go.Scatter(x=df_k.index, y=ma20, line=dict(color="#7c4dff",width=1.5),
            name="MA20",showlegend=False), row=1, col=1)

    fig.update_layout(
        title={"text":f"{sel} 价格走势","x":0.5,"font":{"size":20}},
        height=620, margin=dict(t=60,b=20,l=20,r=20),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#eee", row=1, col=1)
    fig.update_xaxes(gridcolor="#eee", row=2, col=1)
    fig.update_yaxes(gridcolor="#eee", row=1, col=1)
    fig.update_yaxes(gridcolor="#eee", row=2, col=1)

    st.plotly_chart(fig, use_container_width=True)

    # 数据表
    st.divider()
    st.subheader("📄 K线数据明细")
    disp = df_k.tail(30).copy()
    disp["开盘"]=disp["open_price"].apply(lambda x:f"¥{x:,.2f}")
    disp["最高"]=disp["high_price"].apply(lambda x:f"¥{x:,.2f}")
    disp["最低"]=disp["low_price"].apply(lambda x:f"¥{x:,.2f}")
    disp["收盘"]=disp["close_price"].apply(lambda x:f"¥{x:,.2f}")
    disp["涨跌幅"]=disp["change_pct"].apply(lambda x:f"{x:+.2f}%")
    disp["成交量"]=disp["volume"].apply(lambda x:f"{x:,.0f}")
    st.dataframe(disp[["round","开盘","最高","最低","收盘","涨跌幅","成交量"]].rename(
        columns={"round":"轮次"}), use_container_width=True, hide_index=True)

def page_admin_settle():
    render_header()
    st.markdown("### ⚙️ 交易管理")
    stocks = get_stocks()
    if not stocks: st.info("暂无股票"); return

    conn = get_db()
    for s in stocks:
        r = conn.execute("SELECT MAX(round) as r, is_settled FROM rounds WHERE stock_symbol=?",
                         (s["symbol"],)).fetchone()
        conn.close()

        cur_round = r["r"] if r and r["r"] else 1
        settled = r["is_settled"] if r else 1

        # 获取本轮交易
        conn = get_db()
        txns = conn.execute(
            "SELECT COUNT(*) as cnt, SUM(shares) as vol FROM transactions WHERE stock_symbol=? AND round=?",
            (s["symbol"], cur_round)
        ).fetchone()
        conn.close()
        txn_cnt = txns["cnt"] if txns else 0
        txn_vol = txns["vol"] if txns else 0

        with st.container():
            st.markdown(f'<div class="card">', unsafe_allow_html=True)
            c1,c2,c3,c4,c5 = st.columns([2,1,1,1,2])
            c1.markdown(f"**{s['name']}** ({s['symbol']})<br><small>当前价: ¥{s['current_price']:.2f} | 轮次: {cur_round}</small>", unsafe_allow_html=True)
            c2.metric("委托笔数", txn_cnt or 0)
            c3.metric("委托总量", f"{txn_vol or 0}股")
            c4.metric("状态", "✅ 已结算" if settled else "⏳ 待结算")

            if not settled:
                if c5.button("⚡ 结算此轮", key=f"settle_{s['id']}", type="primary"):
                    new_p = settle_round(s["symbol"])
                    st.success(f"✅ {s['name']} 第{cur_round}轮结算完成！新价格: ¥{new_p:.2f}")
                    st.rerun()
            else:
                c5.markdown("---")

            st.markdown('</div>', unsafe_allow_html=True)

    # 定价参数管理
    st.divider()
    st.markdown("### 📐 定价参数调整")
    with st.form("param_form"):
        sel_sym = st.selectbox("选择股票", [f"{s['name']}({s['symbol']})" for s in stocks])
        sym = sel_sym.split("(")[1].rstrip(")")
        s = next(x for x in stocks if x["symbol"]==sym)
        c1,c2 = st.columns(2)
        with c1:
            cp = st.number_input("碳价 (¥)", min_value=0.0, value=float(s["carbon_price"]), step=1.0, format="%.1f")
            icm = st.number_input("行业碳均值 (¥)", min_value=0.0, value=float(s["industry_carbon_mean"]), step=1.0, format="%.1f")
        with c2:
            pr = st.number_input("溢价率 (%)", min_value=0.0, value=float(s["premium_rate"]), step=1.0, format="%.1f")
            sp = st.number_input("昨收价 (手动)" if False else "", value=float(s["previous_close"]), format="%.2f")
        if st.form_submit_button("💾 保存参数", type="primary", use_container_width=True):
            update_stock_params(s["id"], carbon_price=cp, industry_carbon_mean=icm, premium_rate=pr)
            st.success("参数已更新")
            st.rerun()

def page_admin_stock_summary():
    render_header()
    st.markdown("### 📋 股票汇总")
    stats = get_platform_stats()
    cols = st.columns(3)
    cols[0].markdown(metric_card("🏦 总市值",f"¥{stats['total_mv']:,.0f}"), unsafe_allow_html=True)
    dc = "normal" if stats['total_pnl']>=0 else "inverse"
    cols[1].markdown(metric_card("📈 总盈亏",f"¥{stats['total_pnl']:+,.0f}",delta_color=dc), unsafe_allow_html=True)
    cols[2].markdown(metric_card("👥 活跃用户",str(stats['active_users'])), unsafe_allow_html=True)

    summary = get_admin_summary()
    if summary.empty: st.info("暂无数据"); return

    st.divider()
    sdf = summary.sort_values("总盈亏")
    fig = go.Figure(go.Bar(x=sdf["股票名称"], y=sdf["总盈亏"],
        text=sdf["总盈亏"].apply(lambda x:f"¥{x:+,.0f}"),
        marker_color=["#00c853" if v>=0 else "#ff1744" for v in sdf["总盈亏"]]))
    fig.update_traces(textposition="outside")
    fig.update_layout(title={"text":"🏆 盈亏排行","x":0.5}, height=380,
        xaxis_title="",yaxis_title="总盈亏(¥)",plot_bgcolor="rgba(0,0,0,0)",paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    for _, row in summary.iterrows():
        with st.expander(f"🔍 {row['股票名称']}({row['代码']}) — 持仓用户详情"):
            d = get_holder_detail(row["代码"])
            if d.empty: st.info("无用户持有")
            else:
                dd = d.copy()
                dd["成本价"]=dd["成本价"].apply(lambda x:f"¥{x:,.2f}")
                dd["当前价"]=dd["当前价"].apply(lambda x:f"¥{x:,.2f}")
                dd["盈亏"]=dd["盈亏"].apply(lambda x:f"¥{x:+,.2f}")
                dd["收益率"]=dd["收益率"].apply(lambda x:f"{x:+.2f}%")
                st.dataframe(dd, use_container_width=True, hide_index=True)

    disp = summary.copy()
    disp["当前价"]=disp["当前价"].apply(lambda x:f"¥{x:,.2f}")
    disp["总成本"]=disp["总成本"].apply(lambda x:f"¥{x:,.2f}")
    disp["总盈亏"]=disp["总盈亏"].apply(lambda x:f"¥{x:+,.2f}")
    disp["收益率"]=disp["收益率"].apply(lambda x:f"{x:+.2f}%")
    st.dataframe(disp, use_container_width=True, hide_index=True)

def page_admin_stock_mgmt():
    render_header()
    with st.expander("➕ 添加新股票", expanded=False):
        with st.form("add_stock"):
            c1,c2,c3 = st.columns(3)
            with c1: sym = st.text_input("代码", max_chars=10).strip().upper()
            with c2: name = st.text_input("名称").strip()
            with c3: price = st.number_input("初始价", min_value=0.01, step=0.5, format="%.2f")
            if st.form_submit_button("添加", type="primary", use_container_width=True):
                if sym and name:
                    ok, m = add_stock(sym, name, price)
                    st.success(m) if ok else st.error(m)
                    if ok: st.rerun()
                else: st.warning("请完整填写")

    st.divider()
    stocks = get_stocks()
    if not stocks: st.info("暂无股票"); return
    sdf = pd.DataFrame(stocks)
    sdf["current_price"] = sdf["current_price"].apply(lambda x:f"¥{x:,.2f}")
    sdf["last_update"] = sdf["last_update"].apply(lambda x:str(x)[:19] if x else "-")
    d = sdf[["symbol","name","current_price","carbon_price","premium_rate","last_update"]].copy()
    d.columns = ["代码","名称","当前价","碳价","溢价率","更新"]
    st.dataframe(d, use_container_width=True, hide_index=True)

    for s in stocks:
        with st.expander(f"⚡ {s['name']}({s['symbol']})"):
            c1,c2,c3 = st.columns(3)
            with c1:
                st.markdown("**修改价格**")
                np_ = st.number_input("新价格", min_value=0.01, step=0.5, format="%.2f",
                    value=float(s["current_price"]), key=f"pr_{s['id']}")
                if st.button("确认修改", key=f"up_{s['id']}"):
                    conn = get_db()
                    conn.execute("UPDATE stocks SET current_price=?,previous_close=? WHERE id=?",
                                 (np_, np_, s["id"]))
                    conn.commit(); conn.close()
                    st.success(f"已更新"); st.rerun()
            with c2:
                st.markdown("**参数调整**")
                cp = st.number_input("碳价", value=float(s["carbon_price"]), step=1.0, format="%.1f", key=f"cp_{s['id']}")
                pr = st.number_input("溢价率", value=float(s["premium_rate"]), step=1.0, format="%.1f", key=f"pr_{s['id']}")
                if st.button("保存参数", key=f"sv_{s['id']}"):
                    update_stock_params(s["id"], carbon_price=cp, premium_rate=pr)
                    st.success("已保存"); st.rerun()
            with c3:
                st.markdown("**删除股票**")
                if st.button("🗑️ 删除", key=f"del_{s['id']}"):
                    delete_stock(s["id"])
                    st.success("已删除"); st.rerun()

def page_admin_user_mgmt():
    render_header()
    users = get_all_users()
    df = pd.DataFrame(users)
    df["created_at"] = df["created_at"].apply(lambda x:str(x)[:19] if x else "-")
    df.columns = ["ID","用户名","角色","注册时间"]
    df["角色"] = df["角色"].map({"admin":"管理员 👑","player":"选手 🎯"})
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.divider()
    with st.form("reset_pwd"):
        target = st.selectbox("选择用户", [u["username"] for u in users if u["role"]=="player"])
        np_ = st.text_input("新密码", type="password", placeholder="至少4位")
        if st.form_submit_button("重置密码", type="primary", use_container_width=True):
            if target and np_ and len(np_)>=4:
                reset_pwd(target, np_); st.success(f"{target} 密码已重置"); st.rerun()
            else: st.warning("请填写完整")

# ──────────────────────────────────────────────
# 导航
# ──────────────────────────────────────────────
NAV = {
    "📊 总览": page_overview,
    "🏛️ 交易大厅": page_trade_hall,
    "💼 我的持仓": page_portfolio,
    "📈 我的做市": page_market_making,
    "📉 K线展板": page_kline,
    "⚙️ 交易管理": page_admin_settle,
    "📋 股票汇总": page_admin_stock_summary,
    "🔧 股票管理": page_admin_stock_mgmt,
    "👥 用户管理": page_admin_user_mgmt,
}
PLAYER_NAV = ["📊 总览","🏛️ 交易大厅","💼 我的持仓","📈 我的做市","📉 K线展板"]
ADMIN_NAV = list(NAV.keys())

st.set_page_config(page_title="碳交易模拟系统", page_icon="📊", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
init_db()

if "logged_in" not in st.session_state:
    st.session_state.logged_in=False; st.session_state.username=""; st.session_state.role=""

def main():
    if not st.session_state.logged_in: page_login(); return
    with st.sidebar:
        st.markdown("<h3 style='color:#fff;font-weight:700;margin:0'>📊 碳交易</h3>", unsafe_allow_html=True)
        st.markdown(f"<p style='color:rgba(255,255,255,.7);font-size:.85rem'>👤 {st.session_state.username} · {'🛡️ 管理员' if st.session_state.role=='admin' else '🎯 选手'}</p>", unsafe_allow_html=True)
        st.divider()
        nav = ADMIN_NAV if st.session_state.role=="admin" else PLAYER_NAV
        sel = st.radio("菜单", nav, key="nav", label_visibility="collapsed")
        st.divider()
        if st.button("🚪 退出", use_container_width=True):
            st.session_state.logged_in=False; st.rerun()
    for n in nav:
        if n==sel: NAV[n](); break

if __name__ == "__main__":
    main()
