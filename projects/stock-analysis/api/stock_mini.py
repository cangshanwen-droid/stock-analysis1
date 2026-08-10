"""Gipfel Stock Trading API — 精简版，纯 FastAPI + SQLite
支持桌面端统一登录：/auth/login 用户不存在时自动创建（与 Gipfel 管理系统账号同步）
index.html 注入 auto-login 脚本：iframe URL 带 ?token=&username= 时自动写入 localStorage 免登录
"""
import sqlite3, os, time, json, uuid, hashlib
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Gipfel Stock API", version="1.1")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR, html=True), name="static")
app.mount("/_next", StaticFiles(directory=os.path.join(STATIC_DIR, "_next")), name="next-static")

# 自动登录脚本：从 URL 参数读取 token/username → 写入 localStorage（桌面端 iframe 免登录）
AUTO_LOGIN_SCRIPT = """
<script>
(function(){
  try {
    var p = new URLSearchParams(window.location.search);
    var t = p.get('token'), u = p.get('username');
    if (t && u) {
      var r = { token: t, username: u };
      if (p.get('role')) r.role = p.get('role');
      localStorage.setItem('gipfel_session', JSON.stringify(r));
      var clean = window.location.origin + window.location.pathname;
      window.history.replaceState({}, '', clean);
      window.location.reload();
    }
  } catch(e) {}
})();
</script>
"""

@app.get("/", include_in_schema=False)
async def root():
    index_path = os.path.join(STATIC_DIR, "index.html")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            html = f.read()
        if AUTO_LOGIN_SCRIPT not in html:
            html = html.replace("</head>", AUTO_LOGIN_SCRIPT + "</head>", 1)
        return HTMLResponse(html)
    except FileNotFoundError:
        return RedirectResponse("/static/index.html")

DB = os.environ.get("DATABASE_URL", "stocks.db")

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY, symbol TEXT UNIQUE, name TEXT, current_price REAL,
            prev_price REAL, change_pct REAL, sector TEXT, is_active INTEGER DEFAULT 1,
            premium_rate REAL DEFAULT 0, carbon_price REAL DEFAULT 0, revenue REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY, user_id INTEGER, symbol TEXT, side TEXT CHECK(side IN('buy','sell')),
            quantity INTEGER, price REAL, status TEXT DEFAULT 'pending', created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS portfolios (
            user_id INTEGER, symbol TEXT, quantity INTEGER,
            avg_price REAL, PRIMARY KEY(user_id, symbol)
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT,
            balance REAL DEFAULT 100000, role TEXT DEFAULT 'user'
        );
    """)
    db.commit()
    db.close()

@app.on_event("startup")
def startup():
    init_db()
    db = get_db()
    if not db.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]:
        stocks = [
            ("JGONG", "晶工科技", 100, 98),
            ("JXIAO", "佳效集团", 85, 87),
            ("WULIU", "智物流通", 50, 48),
            ("YLIAO", "源料控股", 200, 210),
            ("JIANSHE", "城市建设", 120, 115),
            ("NENGYUAN", "新能源", 150, 160),
        ]
        for s, n, p, pp in stocks:
            db.execute("INSERT OR IGNORE INTO stocks(symbol,name,current_price,prev_price,change_pct,sector) VALUES(?,?,?,?,?,?)",
                       (s, n, p, pp, round((p-pp)/pp*100,2), "综合"))
        db.commit()
    db.close()

@app.get("/market")
def market_data():
    db = get_db()
    rows = [dict(r) for r in db.execute("SELECT * FROM stocks WHERE is_active=1").fetchall()]
    db.close()
    return rows

@app.post("/market/stocks")
async def create_stock(request: Request):
    data = await request.json()
    symbol = (data.get("symbol") or "").upper()
    name = data.get("name", "")
    price = data.get("price") or data.get("initial_price", 100)
    sector = data.get("sector", "综合")
    if not symbol or not name:
        raise HTTPException(400, "缺少 symbol 或 name")
    db = get_db()
    existing = db.execute("SELECT id FROM stocks WHERE symbol=?", (symbol,)).fetchone()
    if existing:
        db.close()
        raise HTTPException(409, f"股票 {symbol} 已存在")
    db.execute(
        "INSERT INTO stocks(symbol,name,current_price,prev_price,change_pct,sector) VALUES(?,?,?,?,?,?)",
        (symbol, name, float(price), float(price), 0, sector))
    db.commit()
    stock_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = dict(db.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone())
    db.close()
    return row

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.1", "service": "stock-trading"}

@app.post("/auth/login")
async def auth_login(request: Request):
    """统一登录：与 Gipfel 管理系统共用账号。
    用户不存在时自动创建（桌面端账号同步），返回 token + role。
    """
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise HTTPException(400, "缺少用户名或密码")
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        # 自动创建（桌面端账号同步）
        db.execute("INSERT INTO users(username,password,balance,role) VALUES(?,?,100000,'user')",
                   (username, hashlib.sha256(password.encode()).hexdigest()))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    elif user["password"] != hashlib.sha256(password.encode()).hexdigest():
        db.close()
        raise HTTPException(401, "密码错误")
    db.close()
    return {"token": str(uuid.uuid4()), "user": dict(user)}

@app.post("/auth/register")
async def auth_register(request: Request):
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = data.get("role") or "user"
    if not username or not password:
        raise HTTPException(400, "缺少用户名或密码")
    db = get_db()
    exists = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if exists:
        db.close()
        raise HTTPException(409, "用户名已存在")
    db.execute("INSERT INTO users(username,password,balance,role) VALUES(?,?,100000,?)",
               (username, hashlib.sha256(password.encode()).hexdigest(), role))
    db.commit()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    db.close()
    return {"token": str(uuid.uuid4()), "user": dict(user)}


# ── 管理端：账户监控（桌面端 admin 面板调用）──

def _require_admin(request: Request) -> str:
    """简易管理员校验：请求头 X-Admin-Key 匹配环境变量 ADMIN_KEY（默认 'gipfel-admin-dev'）"""
    key = request.headers.get("X-Admin-Key", "")
    expected = os.environ.get("ADMIN_KEY", "gipfel-admin-dev")
    if key != expected:
        raise HTTPException(403, "无管理权限")
    return key


@app.get("/admin/accounts")
async def admin_accounts(request: Request):
    """所有账户总览：用户 + 余额 + 持仓市值 + 订单数"""
    _require_admin(request)
    db = get_db()
    users = db.execute("SELECT id, username, balance, role FROM users ORDER BY id").fetchall()
    positions = db.execute(
        "SELECT p.user_id, p.symbol, p.quantity, p.avg_price, s.current_price "
        "FROM portfolios p LEFT JOIN stocks s ON p.symbol = s.symbol"
    ).fetchall()
    orders = db.execute(
        "SELECT user_id, COUNT(*) AS cnt, "
        "SUM(CASE WHEN side='buy' THEN quantity ELSE 0 END) AS buy_qty, "
        "SUM(CASE WHEN side='sell' THEN quantity ELSE 0 END) AS sell_qty "
        "FROM orders GROUP BY user_id"
    ).fetchall()
    db.close()
    pos_map: dict[int, list] = {}
    for p in positions:
        pos_map.setdefault(p["user_id"], []).append(dict(p))
    order_map: dict[int, dict] = {o["user_id"]: dict(o) for o in orders}
    result = []
    for u in users:
        uid = u["id"]
        pos = pos_map.get(uid, [])
        market_value = sum(
            (p["current_price"] or p["avg_price"] or 0) * p["quantity"] for p in pos
        )
        result.append({
            "id": uid,
            "username": u["username"],
            "role": u["role"],
            "balance": u["balance"],
            "position_count": len(pos),
            "market_value": round(market_value, 2),
            "total_assets": round((u["balance"] or 0) + market_value, 2),
            "orders": order_map.get(uid, {"cnt": 0, "buy_qty": 0, "sell_qty": 0}),
        })
    return result


@app.get("/admin/accounts/{user_id}")
async def admin_account_detail(user_id: int, request: Request):
    """单个账户详情：用户信息 + 持仓明细 + 订单历史"""
    _require_admin(request)
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        db.close()
        raise HTTPException(404, "账户不存在")
    positions = db.execute(
        "SELECT p.symbol, p.quantity, p.avg_price, s.current_price, s.name "
        "FROM portfolios p LEFT JOIN stocks s ON p.symbol = s.symbol WHERE p.user_id=?",
        (user_id,)
    ).fetchall()
    orders = db.execute(
        "SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
        (user_id,)
    ).fetchall()
    db.close()
    return {
        "user": dict(user),
        "positions": [dict(p) for p in positions],
        "orders": [dict(o) for o in orders],
    }


@app.get("/admin/stocks")
async def admin_stocks(request: Request):
    """管理端股票总览"""
    _require_admin(request)
    db = get_db()
    rows = [dict(r) for r in db.execute("SELECT * FROM stocks ORDER BY symbol").fetchall()]
    db.close()
    return rows


@app.patch("/admin/stocks/{symbol}")
async def admin_stock_update(symbol: str, request: Request):
    """同步区域经济指标到股票：premium_rate(幸福度) / carbon_price(碳排) / revenue(人口)
    同时按指标调整现价：幸福度↑→价格↑，碳排↑→价格↓，幅度 ±2% 内"""
    _require_admin(request)
    data = await request.json()
    sym = symbol.upper()
    db = get_db()
    stock = db.execute("SELECT * FROM stocks WHERE symbol=?", (sym,)).fetchone()
    if not stock:
        db.close()
        raise HTTPException(404, f"股票 {sym} 不存在")
    updates = []
    params = []
    for key in ("premium_rate", "carbon_price", "revenue"):
        if key in data:
            updates.append(f"{key}=?")
            params.append(float(data[key]))
    if updates:
        params.append(sym)
        db.execute(f"UPDATE stocks SET {', '.join(updates)} WHERE symbol=?", params)
        # 价格联动：premium_rate 上调→涨，carbon_price 上调→跌
        row = db.execute("SELECT * FROM stocks WHERE symbol=?", (sym,)).fetchone()
        price = row["current_price"]
        drift = 0.0
        if row["premium_rate"]:
            drift += (row["premium_rate"] - 50) / 50 * 0.02  # 幸福度高于50上调
        if row["carbon_price"]:
            drift -= row["carbon_price"] / 500 * 0.02        # 碳排价格上涨下跌
        new_price = max(0.01, round(price * (1 + drift), 2))
        db.execute("UPDATE stocks SET prev_price=?, current_price=?, change_pct=? WHERE symbol=?",
                   (price, new_price, round((new_price - price) / price * 100, 2), sym))
        db.commit()
    result = dict(db.execute("SELECT * FROM stocks WHERE symbol=?", (sym,)).fetchone())
    db.close()
    return {"accepted": True, "stock": result}
