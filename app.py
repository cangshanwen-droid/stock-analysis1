"""
股票分析平台 — 单文件部署版
可直接部署到 Streamlit Cloud，使用 SQLite 数据
"""
import os
import sqlite3
import hashlib
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.express as px

# ============================================================
# 数据库路径（Streamlit Cloud 兼容）
# ============================================================
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")


# ============================================================
# 数据库层
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_pwd(password):
    return hashlib.sha256(password.encode()).hexdigest()


def init_db():
    """建表 + 种子数据（幂等）"""
    conn = get_db()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT UNIQUE NOT NULL,
            password   TEXT NOT NULL,
            role       TEXT NOT NULL DEFAULT 'player',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS stocks (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol        TEXT UNIQUE NOT NULL,
            name          TEXT NOT NULL,
            current_price REAL NOT NULL DEFAULT 0,
            is_deleted    INTEGER NOT NULL DEFAULT 0,
            last_update   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT NOT NULL,
            stock_symbol TEXT NOT NULL,
            trade_type   TEXT NOT NULL,
            price        REAL NOT NULL,
            shares       INTEGER NOT NULL,
            trade_date   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 仅首次运行插入种子数据
    if cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        cur.execute("INSERT INTO users (username, password, role) VALUES (?,?,?)",
                    ("admin", hash_pwd("admin123"), "admin"))
        for u in ("player1", "player2", "player3"):
            cur.execute("INSERT INTO users (username, password, role) VALUES (?,?,?)",
                        (u, hash_pwd(u), "player"))
        for sym, name, price in [("TSLA", "特斯拉", 250.0), ("AAPL", "苹果", 175.0), ("NVDA", "英伟达", 450.0)]:
            cur.execute("INSERT INTO stocks (symbol, name, current_price) VALUES (?,?,?)",
                        (sym, name, price))
        for args in [
            ("player1", "TSLA", "buy", 200.0, 100),
            ("player1", "AAPL", "buy", 150.0, 50),
            ("player1", "TSLA", "sell", 240.0, 80),
            ("player2", "NVDA", "buy", 400.0, 30),
            ("player2", "AAPL", "sell", 160.0, 40),
            ("player3", "TSLA", "buy", 210.0, 50),
            ("player3", "NVDA", "buy", 420.0, 20),
        ]:
            cur.execute(
                "INSERT INTO transactions (username, stock_symbol, trade_type, price, shares) VALUES (?,?,?,?,?)",
                args,
            )

    conn.commit()
    conn.close()


# ============================================================
# 用户操作
# ============================================================

def auth_user(username, password):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if row and row["password"] == hash_pwd(password):
        return True, row["role"]
    return False, ""


def register_user(username, password, role="player"):
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (username, password, role) VALUES (?,?,?)",
                     (username, hash_pwd(password), role))
        conn.commit()
        return True, "注册成功"
    except sqlite3.IntegrityError:
        return False, "用户名已存在"
    finally:
        conn.close()


def get_all_users():
    conn = get_db()
    rows = conn.execute("SELECT id, username, role, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def reset_user_password(username, new_password):
    conn = get_db()
    conn.execute("UPDATE users SET password=? WHERE username=?", (hash_pwd(new_password), username))
    conn.commit()
    conn.close()


# ============================================================
# 股票操作
# ============================================================

def get_active_stocks():
    conn = get_db()
    rows = conn.execute("SELECT * FROM stocks WHERE is_deleted=0 ORDER BY symbol").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stock_by_id(sid):
    conn = get_db()
    row = conn.execute("SELECT * FROM stocks WHERE id=?", (sid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def add_stock(symbol, name, price):
    conn = get_db()
    try:
        conn.execute("INSERT INTO stocks (symbol, name, current_price) VALUES (?,?,?)",
                     (symbol.upper(), name, price))
        conn.commit()
        return True, "添加成功"
    except sqlite3.IntegrityError:
        return False, "股票代码已存在"
    finally:
        conn.close()


def update_stock_price(sid, new_price):
    conn = get_db()
    conn.execute("UPDATE stocks SET current_price=?, last_update=? WHERE id=?",
                 (new_price, datetime.now(), sid))
    conn.commit()
    conn.close()


def soft_delete_stock(sid):
    conn = get_db()
    conn.execute("UPDATE stocks SET is_deleted=1, last_update=? WHERE id=?",
                 (datetime.now(), sid))
    conn.commit()
    conn.close()


def force_close_stock(sid):
    conn = get_db()
    stock = conn.execute("SELECT * FROM stocks WHERE id=?", (sid,)).fetchone()
    if not stock:
        conn.close()
        return False
    symbol, price = stock["symbol"], stock["current_price"]
    players = conn.execute("SELECT username FROM users WHERE role='player'").fetchall()
    for p in players:
        net = _net_position(conn, p["username"], symbol)
        if net > 0:
            conn.execute(
                "INSERT INTO transactions (username, stock_symbol, trade_type, price, shares) VALUES (?,?,?,?,?)",
                (p["username"], symbol, "force_close", price, net),
            )
    conn.execute("UPDATE stocks SET is_deleted=1, last_update=? WHERE id=?", (datetime.now(), sid))
    conn.commit()
    conn.close()
    return True


def _net_position(conn, username, symbol):
    buy = conn.execute(
        "SELECT COALESCE(SUM(shares),0) FROM transactions WHERE username=? AND stock_symbol=? AND trade_type='buy'",
        (username, symbol),
    ).fetchone()[0]
    sell = conn.execute(
        "SELECT COALESCE(SUM(shares),0) FROM transactions WHERE username=? AND stock_symbol=? AND trade_type IN ('sell','force_close')",
        (username, symbol),
    ).fetchone()[0]
    return buy - sell


# ============================================================
# 交易 & 持仓计算
# ============================================================

def add_transaction(username, stock_symbol, trade_type, price, shares):
    conn = get_db()
    conn.execute(
        "INSERT INTO transactions (username, stock_symbol, trade_type, price, shares) VALUES (?,?,?,?,?)",
        (username, stock_symbol, trade_type, price, shares),
    )
    conn.commit()
    conn.close()


def _build_price_map():
    return {s["symbol"]: {"name": s["name"], "price": s["current_price"]}
            for s in get_active_stocks()}


def get_user_portfolio(username):
    """净持仓 > 0 的股票"""
    conn = get_db()
    buys = conn.execute(
        """SELECT stock_symbol, SUM(shares) AS total_shares, SUM(price*shares) AS total_cost
           FROM transactions WHERE username=? AND trade_type='buy' GROUP BY stock_symbol""",
        (username,),
    ).fetchall()
    sells = conn.execute(
        """SELECT stock_symbol, SUM(shares) AS sold
           FROM transactions WHERE username=? AND trade_type IN ('sell','force_close') GROUP BY stock_symbol""",
        (username,),
    ).fetchall()
    conn.close()

    sell_map = {r["stock_symbol"]: r["sold"] for r in sells}
    pmap = _build_price_map()
    rows = []
    for b in buys:
        sym = b["stock_symbol"]
        net = b["total_shares"] - sell_map.get(sym, 0)
        if net <= 0:
            continue
        avg = b["total_cost"] / b["total_shares"]
        info = pmap.get(sym, {"name": sym, "price": avg})
        cp = info["price"]
        pnl = (cp - avg) * net
        rows.append({
            "symbol": sym, "name": info["name"], "shares": int(net),
            "avg_cost": round(avg, 2), "current_price": cp,
            "market_value": round(cp * net, 2), "pnl": round(pnl, 2),
            "pnl_ratio": round((pnl / (avg * net)) * 100, 2) if avg > 0 else 0,
        })
    return pd.DataFrame(rows)


def get_user_market_making(username):
    conn = get_db()
    rows = conn.execute(
        """SELECT t.stock_symbol, t.price AS sell_price, t.shares, t.trade_date,
                  COALESCE(s.current_price, t.price) AS current_price,
                  COALESCE(s.name, t.stock_symbol) AS stock_name
           FROM transactions t
           LEFT JOIN stocks s ON t.stock_symbol = s.symbol
           WHERE t.username=? AND t.trade_type='sell'
           ORDER BY t.trade_date DESC""",
        (username,),
    ).fetchall()
    conn.close()
    data = []
    for r in rows:
        data.append({
            "股票": r["stock_name"],
            "卖出价": round(r["sell_price"], 2),
            "当前价": round(r["current_price"], 2),
            "数量": r["shares"],
            "对手方盈亏": round((r["current_price"] - r["sell_price"]) * r["shares"], 2),
            "交易时间": r["trade_date"],
        })
    return pd.DataFrame(data)


def get_user_force_close(username):
    conn = get_db()
    rows = conn.execute(
        """SELECT t.stock_symbol, t.price, t.shares, t.trade_date,
                  COALESCE(s.name, t.stock_symbol) AS stock_name
           FROM transactions t LEFT JOIN stocks s ON t.stock_symbol = s.symbol
           WHERE t.username=? AND t.trade_type='force_close'
           ORDER BY t.trade_date DESC""",
        (username,),
    ).fetchall()
    conn.close()
    return pd.DataFrame([
        {"股票": r["stock_name"], "平仓价": r["price"], "数量": r["shares"], "时间": r["trade_date"]}
        for r in rows
    ])


def get_user_overview(username):
    pf = get_user_portfolio(username)
    if pf.empty:
        return {"total_assets": 0, "total_cost": 0, "total_pnl": 0, "pnl_ratio": 0, "stock_count": 0, "stock_pnl": []}
    ta = pf["market_value"].sum()
    tc = (pf["avg_cost"] * pf["shares"]).sum()
    tp = ta - tc
    return {
        "total_assets": round(ta, 2), "total_cost": round(tc, 2), "total_pnl": round(tp, 2),
        "pnl_ratio": round((tp / tc) * 100, 2) if tc > 0 else 0,
        "stock_count": len(pf), "stock_pnl": pf[["name", "symbol", "pnl"]].to_dict("records"),
    }


# ============================================================
# 管理员汇总
# ============================================================

def get_admin_stock_summary():
    stocks = get_active_stocks()
    if not stocks:
        return pd.DataFrame()
    conn = get_db()
    players = conn.execute("SELECT username FROM users WHERE role='player'").fetchall()
    conn.close()
    all_pfs = {}
    for p in players:
        df = get_user_portfolio(p["username"])
        if not df.empty:
            all_pfs[p["username"]] = df

    summary = []
    for s in stocks:
        sym = s["symbol"]
        total_shares = 0
        total_cost_basis = 0.0
        total_pnl = 0.0
        cnt = 0
        for uname, pf in all_pfs.items():
            row = pf[pf["symbol"] == sym]
            if row.empty:
                continue
            r = row.iloc[0]
            total_shares += r["shares"]
            total_cost_basis += r["avg_cost"] * r["shares"]
            total_pnl += r["pnl"]
            cnt += 1
        pct = round((total_pnl / total_cost_basis) * 100, 2) if cnt and total_cost_basis else 0
        summary.append({
            "股票名称": s["name"], "股票代码": sym, "当前价": s["current_price"],
            "持有用户数": cnt, "总持仓量": int(total_shares),
            "总成本": round(total_cost_basis, 2), "总盈亏": round(total_pnl, 2), "收益率(%)": pct,
        })
    return pd.DataFrame(summary)


def get_stock_holder_details(stock_symbol):
    conn = get_db()
    players = conn.execute("SELECT username FROM users WHERE role='player'").fetchall()
    conn.close()
    pmap = _build_price_map()
    details = []
    for p in players:
        pf = get_user_portfolio(p["username"])
        if pf.empty:
            continue
        row = pf[pf["symbol"] == stock_symbol]
        if row.empty:
            continue
        r = row.iloc[0]
        details.append({
            "用户名": p["username"], "持仓量": int(r["shares"]), "成本价": r["avg_cost"],
            "当前价": r["current_price"], "盈亏": r["pnl"], "收益率(%)": r["pnl_ratio"],
        })
    return pd.DataFrame(details)


def get_platform_stats():
    summary = get_admin_stock_summary()
    if summary.empty:
        return {"total_market_value": 0, "total_pnl": 0, "active_users": 0}
    conn = get_db()
    cnt = conn.execute("SELECT COUNT(*) FROM users WHERE role='player'").fetchone()[0]
    conn.close()
    return {
        "total_market_value": round((summary["当前价"] * summary["总持仓量"]).sum(), 2),
        "total_pnl": round(summary["总盈亏"].sum(), 2), "active_users": cnt,
    }


# ============================================================
# Streamlit 界面
# ============================================================

st.set_page_config(page_title="股票分析平台", page_icon="📊", layout="wide")
init_db()

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""


def logout():
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""


def fmt_color(val, suf=""):
    if val > 0:
        return f"<span style='color:#00cc66;font-weight:bold'>+{val:,.2f}{suf}</span>"
    if val < 0:
        return f"<span style='color:#e74c3c;font-weight:bold'>{val:,.2f}{suf}</span>"
    return f"<span style='color:gray'>{val:,.2f}{suf}</span>"


# ============================================================
# 登录 / 注册
# ============================================================

def auth_page():
    _, mid, _ = st.columns([1, 2.5, 1])
    with mid:
        st.markdown("<h1 style='text-align:center;color:#1a5276'>📊 股票分析平台</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align:center;color:#888'>商业竞赛模拟系统</p>", unsafe_allow_html=True)
        st.divider()
        tab1, tab2 = st.tabs(["🔐 登录", "📝 注册"])

        with tab1:
            with st.form("login_form"):
                u = st.text_input("用户名", placeholder="输入用户名")
                p = st.text_input("密码", type="password", placeholder="输入密码")
                if st.form_submit_button("登录", use_container_width=True, type="primary"):
                    if not u or not p:
                        st.error("请填写完整")
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
            with st.form("register_form"):
                u2 = st.text_input("用户名", placeholder="至少3位", key="reg_u")
                p2 = st.text_input("密码", type="password", placeholder="至少4位", key="reg_p")
                p3 = st.text_input("确认密码", type="password", key="reg_p2")
                if st.form_submit_button("注册", use_container_width=True):
                    if not u2 or not p2:
                        st.error("请填写完整")
                    elif len(u2) < 3:
                        st.error("用户名至少3位")
                    elif len(p2) < 4:
                        st.error("密码至少4位")
                    elif p2 != p3:
                        st.error("两次密码不一致")
                    else:
                        ok, msg = register_user(u2, p2)
                        st.success(msg) if ok else st.error(msg)


# ============================================================
# 页面：个人总览
# ============================================================

def page_overview():
    st.title("📊 个人总览")

    if st.session_state.role == "admin":
        stats = get_platform_stats()
        col1, col2, col3 = st.columns(3)
        col1.metric("🏦 总市值", f"¥{stats['total_market_value']:,.2f}")
        dc = "normal" if stats["total_pnl"] >= 0 else "inverse"
        col2.metric("📈 平台总盈亏", f"¥{stats['total_pnl']:,.2f}", delta_color=dc)
        col3.metric("👥 活跃用户数", stats["active_users"])
        st.divider()
        summary = get_admin_stock_summary()
        if not summary.empty:
            st.subheader("🏆 各股票盈亏排行")
            sdf = summary.sort_values("总盈亏")
            fig = px.bar(sdf, x="股票名称", y="总盈亏", text="总盈亏", color="总盈亏",
                         color_continuous_scale=["#e74c3c", "#f39c12", "#00cc66"], height=400)
            fig.update_traces(texttemplate="%{text:+,.0f}", textposition="outside")
            fig.update_layout(xaxis_title="", yaxis_title="盈亏 (¥)", margin=dict(t=20))
            st.plotly_chart(fig, use_container_width=True)
        return

    data = get_user_overview(st.session_state.username)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("💰 总资产", f"¥{data['total_assets']:,.2f}")
    col2.metric("📉 总成本", f"¥{data['total_cost']:,.2f}")
    dc = "normal" if data["total_pnl"] >= 0 else "inverse"
    col3.metric("📈 总盈亏", f"¥{data['total_pnl']:,.2f}", delta=f"{data['pnl_ratio']:+.2f}%", delta_color=dc)
    col4.metric("🧾 持仓股票数", data["stock_count"])

    if data["stock_pnl"]:
        st.divider()
        st.subheader("📊 各股票盈亏对比")
        df = pd.DataFrame(data["stock_pnl"])
        fig = px.bar(df, x="name", y="pnl", text="pnl",
                     color=df["pnl"].apply(lambda x: "盈利" if x >= 0 else "亏损"),
                     color_discrete_map={"盈利": "#00cc66", "亏损": "#e74c3c"}, height=400)
        fig.update_traces(texttemplate="%{text:+,.0f}", textposition="outside")
        fig.update_layout(xaxis_title="", yaxis_title="盈亏 (¥)", showlegend=False, margin=dict(t=20))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("暂无持仓数据，请先买入股票")


# ============================================================
# 页面：我的持仓
# ============================================================

def page_portfolio():
    st.title("💼 我的持仓")
    pf = get_user_portfolio(st.session_state.username)
    if pf.empty:
        st.info("暂无持仓，请通过「新增交易」买入股票")
        return
    d = pf[["name", "shares", "avg_cost", "current_price", "market_value", "pnl", "pnl_ratio"]].copy()
    d.columns = ["股票名称", "持仓量", "成本价", "当前价", "市值", "盈亏", "收益率"]
    d["成本价"] = d["成本价"].apply(lambda x: f"¥{x:,.2f}")
    d["当前价"] = d["当前价"].apply(lambda x: f"¥{x:,.2f}")
    d["市值"] = d["市值"].apply(lambda x: f"¥{x:,.2f}")
    d["盈亏"] = d["盈亏"].apply(lambda x: f"¥{x:+,.2f}")
    d["收益率"] = d["收益率"].apply(lambda x: f"{x:+.2f}%")
    st.dataframe(d, use_container_width=True, hide_index=True)

    tv = pf["market_value"].sum()
    tc = (pf["avg_cost"] * pf["shares"]).sum()
    tp = tv - tc
    col1, col2, col3 = st.columns(3)
    col1.metric("总市值", f"¥{tv:,.2f}")
    col2.metric("总成本", f"¥{tc:,.2f}")
    col3.metric("总盈亏", f"¥{tp:,.2f}", delta=f"{tp/tc*100:+.2f}%" if tc else "0%",
                delta_color="normal" if tp >= 0 else "inverse")


# ============================================================
# 页面：我的做市
# ============================================================

def page_market_making():
    st.title("📈 我的做市")
    st.caption("你卖出给客户（做市商角色）的交易记录")
    mm = get_user_market_making(st.session_state.username)
    fc = get_user_force_close(st.session_state.username)
    tab1, tab2 = st.tabs(["📤 做市卖出", "⚠️ 强制平仓"])

    with tab1:
        if mm.empty:
            st.info("尚无做市卖出记录")
        else:
            d = mm.copy()
            d["卖出价"] = d["卖出价"].apply(lambda x: f"¥{x:,.2f}")
            d["当前价"] = d["当前价"].apply(lambda x: f"¥{x:,.2f}")
            d["对手方盈亏"] = d["对手方盈亏"].apply(lambda x: f"¥{x:+,.2f}")
            st.dataframe(d, use_container_width=True, hide_index=True)
            st.metric("客户总盈亏", f"¥{mm['对手方盈亏'].sum():+,.2f}")

    with tab2:
        if fc.empty:
            st.info("无强制平仓记录")
        else:
            st.warning("管理员强制平仓时自动生成")
            fc["平仓价"] = fc["平仓价"].apply(lambda x: f"¥{x:,.2f}")
            st.dataframe(fc, use_container_width=True, hide_index=True)


# ============================================================
# 页面：新增交易
# ============================================================

def page_trade():
    st.title("➕ 新增交易")
    stocks = get_active_stocks()
    if not stocks:
        st.error("暂无可用股票，请联系管理员")
        return
    opts = {f"{s['name']} ({s['symbol']})": s["symbol"] for s in stocks}

    with st.form("trade_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            sel = st.selectbox("选择股票", list(opts.keys()))
            direction = st.radio("方向", ["买入 (持仓)", "卖出 (做市)"], horizontal=True)
        with c2:
            price = st.number_input("价格 (元/股)", min_value=0.01, step=0.5, format="%.2f")
            shares = st.number_input("数量 (股)", min_value=1, step=1, format="%d")
        submitted = st.form_submit_button("确认提交", type="primary", use_container_width=True)

    if submitted:
        tt = "buy" if "买入" in direction else "sell"
        add_transaction(st.session_state.username, opts[sel], tt, price, shares)
        st.success(f"{'买入' if tt == 'buy' else '卖出'} {shares} 股 {sel.split('(')[0].strip()} 成功！")
        st.rerun()

    st.divider()
    st.subheader("📋 近期交易")
    conn = get_db()
    rows = conn.execute(
        """SELECT t.stock_symbol, t.trade_type, t.price, t.shares, t.trade_date,
                  COALESCE(s.name, t.stock_symbol) AS stock_name
           FROM transactions t LEFT JOIN stocks s ON t.stock_symbol = s.symbol
           WHERE t.username=? ORDER BY t.trade_date DESC LIMIT 20""",
        (st.session_state.username,),
    ).fetchall()
    conn.close()
    if rows:
        records = []
        mp = {"buy": "买入", "sell": "卖出(做市)", "force_close": "强制平仓"}
        for r in rows:
            records.append({
                "股票": r["stock_name"], "方向": mp.get(r["trade_type"], r["trade_type"]),
                "价格": f"¥{r['price']:,.2f}", "数量": r["shares"], "时间": r["trade_date"],
            })
        st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
    else:
        st.info("暂无交易记录")


# ============================================================
# 页面：股票汇总（管理员）
# ============================================================

def page_admin_stock_summary():
    st.title("📋 股票汇总")
    st.caption("所有选手在各股票上的持仓情况")

    stats = get_platform_stats()
    col1, col2, col3 = st.columns(3)
    col1.metric("🏦 总市值", f"¥{stats['total_market_value']:,.2f}")
    dc = "normal" if stats["total_pnl"] >= 0 else "inverse"
    col2.metric("📈 总盈亏", f"¥{stats['total_pnl']:,.2f}", delta_color=dc)
    col3.metric("👥 活跃用户数", stats["active_users"])

    summary = get_admin_stock_summary()
    if summary.empty:
        st.info("暂无持仓数据")
        return

    st.divider()
    st.subheader("🏆 各股票盈亏排行")
    sdf = summary.sort_values("总盈亏")
    fig = px.bar(sdf, x="股票名称", y="总盈亏", text="总盈亏", color="总盈亏",
                 color_continuous_scale=["#e74c3c", "#f39c12", "#00cc66"], height=420)
    fig.update_traces(texttemplate="%{text:+,.0f}", textposition="outside")
    fig.update_layout(xaxis_title="", yaxis_title="盈亏 (¥)", margin=dict(t=20))
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("📄 详细数据")
    for _, row in summary.iterrows():
        with st.expander(f"🔍 {row['股票名称']} ({row['股票代码']}) — 查看持仓用户"):
            d = get_stock_holder_details(row["股票代码"])
            if d.empty:
                st.info("无用户持有")
            else:
                dd = d.copy()
                dd["成本价"] = dd["成本价"].apply(lambda x: f"¥{x:,.2f}")
                dd["当前价"] = dd["当前价"].apply(lambda x: f"¥{x:,.2f}")
                dd["盈亏"] = dd["盈亏"].apply(lambda x: f"¥{x:+,.2f}")
                dd["收益率(%)"] = dd["收益率(%)"].apply(lambda x: f"{x:+.2f}%")
                st.dataframe(dd, use_container_width=True, hide_index=True)

    disp = summary.copy()
    disp["当前价"] = disp["当前价"].apply(lambda x: f"¥{x:,.2f}")
    disp["总成本"] = disp["总成本"].apply(lambda x: f"¥{x:,.2f}")
    disp["总盈亏"] = disp["总盈亏"].apply(lambda x: f"¥{x:+,.2f}")
    disp["收益率(%)"] = disp["收益率(%)"].apply(lambda x: f"{x:+.2f}%")
    st.dataframe(disp, use_container_width=True, hide_index=True)


# ============================================================
# 页面：股票管理（管理员）
# ============================================================

def page_admin_stock_mgmt():
    st.title("⚙️ 股票管理")

    with st.expander("➕ 添加新股票", expanded=False):
        with st.form("add_stock"):
            c1, c2, c3 = st.columns(3)
            with c1:
                sym = st.text_input("代码", placeholder="AAPL", max_chars=10).strip().upper()
            with c2:
                name = st.text_input("名称", placeholder="苹果").strip()
            with c3:
                price = st.number_input("价格", min_value=0.01, step=0.5, format="%.2f")
            if st.form_submit_button("添加", type="primary", use_container_width=True):
                if sym and name and price > 0:
                    ok, msg = add_stock(sym, name, price)
                    st.success(msg) if ok else st.error(msg)
                    if ok:
                        st.rerun()
                else:
                    st.warning("请完整填写")

    st.divider()
    st.subheader("📋 股票列表")
    stocks = get_active_stocks()
    if not stocks:
        st.info("暂无股票")
        return

    sdf = pd.DataFrame(stocks)
    sdf["current_price"] = sdf["current_price"].apply(lambda x: f"¥{x:,.2f}")
    sdf["last_update"] = sdf["last_update"].apply(lambda x: str(x)[:19] if x else "-")
    d = sdf[["symbol", "name", "current_price", "last_update"]].copy()
    d.columns = ["代码", "名称", "当前价", "最后更新"]
    st.dataframe(d, use_container_width=True, hide_index=True)

    for s in stocks:
        sid, sym, name = s["id"], s["symbol"], s["name"]
        with st.expander(f"⚡ {name} ({sym})"):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**修改价格**")
                np_ = st.number_input("新价格", min_value=0.01, step=0.5, format="%.2f",
                                      value=float(s["current_price"]), key=f"np_{sid}")
                if st.button("确认修改", key=f"up_{sid}"):
                    update_stock_price(sid, np_)
                    st.success(f"已更新为 ¥{np_:,.2f}")
                    st.rerun()
            with c2:
                st.markdown("**软删除**")
                st.caption("标记删除，历史数据保留")
                if st.button("🗑️ 标记删除", key=f"sd_{sid}"):
                    soft_delete_stock(sid)
                    st.success("已标记删除")
                    st.rerun()
            with c3:
                st.markdown("**强制平仓**")
                holders = get_stock_holder_details(sym)
                if not holders.empty:
                    st.warning(f"⚠️ {len(holders)} 位用户持有")
                st.caption("以当前价卖出所有持仓后删除")
                if st.button("⚡ 强制平仓并删除", key=f"fc_{sid}", type="primary"):
                    if not holders.empty:
                        force_close_stock(sid)
                        st.success("已强制平仓并删除")
                    else:
                        soft_delete_stock(sid)
                        st.success("无持仓，已删除")
                    st.rerun()


# ============================================================
# 页面：用户管理（管理员）
# ============================================================

def page_admin_user_mgmt():
    st.title("👥 用户管理")
    users = get_all_users()
    df = pd.DataFrame(users)
    df["created_at"] = df["created_at"].apply(lambda x: str(x)[:19] if x else "-")
    df.columns = ["ID", "用户名", "角色", "注册时间"]
    df["角色"] = df["角色"].map({"admin": "管理员 👑", "player": "选手 🎯"})
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🔑 重置密码")
    with st.form("reset_pwd"):
        target = st.selectbox("选择用户", [u["username"] for u in users if u["role"] == "player"])
        new_pwd = st.text_input("新密码", type="password", placeholder="至少4位")
        if st.form_submit_button("重置密码", type="primary", use_container_width=True):
            if target and new_pwd and len(new_pwd) >= 4:
                reset_user_password(target, new_pwd)
                st.success(f"{target} 密码已重置")
                st.rerun()
            else:
                st.warning("请填写完整信息")


# ============================================================
# 导航 & 主入口
# ============================================================

PAGES = {
    "📊 总览":     page_overview,
    "💼 我的持仓":  page_portfolio,
    "📈 我的做市":  page_market_making,
    "➕ 新增交易":  page_trade,
    "📋 股票汇总":  page_admin_stock_summary,
    "⚙️ 股票管理":  page_admin_stock_mgmt,
    "👥 用户管理":  page_admin_user_mgmt,
}

PLAYER_NAV = list(PAGES.items())[:4]
ADMIN_NAV = list(PAGES.items())


def main():
    if not st.session_state.logged_in:
        auth_page()
        return

    with st.sidebar:
        st.markdown("<h3 style='color:#1a5276'>📊 股票分析平台</h3>", unsafe_allow_html=True)
        st.divider()
        st.markdown(f"👤 **{st.session_state.username}**")
        st.markdown("🛡️ `管理员`" if st.session_state.role == "admin" else "🎯 `选手`")
        st.divider()
        nav = ADMIN_NAV if st.session_state.role == "admin" else PLAYER_NAV
        labels = [n[0] for n in nav]
        sel = st.radio("导航菜单", labels, key="nav")
        st.divider()
        if st.button("🚪 退出登录", use_container_width=True):
            logout()
            st.rerun()

    for label, fn in nav:
        if label == sel:
            fn()
            break


if __name__ == "__main__":
    main()
