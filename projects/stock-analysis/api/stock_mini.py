"""Gipfel Stock Trading API — 精简版，纯 FastAPI + SQLite
支持桌面端统一登录：/auth/login 用户不存在时自动创建（与 Gipfel 管理系统账号同步）
index.html 注入 auto-login 脚本：iframe URL 带 ?token=&username= 时自动写入 localStorage 免登录
"""
import sqlite3, os, json, uuid
from fastapi import FastAPI, HTTPException, Request, Header, Depends
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
    """v1.3.1 并发加固：WAL 模式（读写并发不锁库）+ busy_timeout 5s（写锁等待）。
    与 gipfel-api 对齐——买卖/资金高频写场景 delete 模式会写锁阻塞。"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
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
    # ── v1.3.1 幂等迁移：orders 加 idempotency_key 列 + 部分唯一索引（空 key 不约束）──
    cols = [c[1] for c in db.execute("PRAGMA table_info(orders)").fetchall()]
    if "idempotency_key" not in cols:
        db.execute("ALTER TABLE orders ADD COLUMN idempotency_key TEXT DEFAULT ''")
        db.commit()
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_idem ON orders(idempotency_key) "
               "WHERE idempotency_key != ''")
    db.commit()
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
    # 安全验收 P0：上市公司创建必须管理密钥（桌面端 CompanyListPage 上市流程
    # 经 stock-sync 带 X-Admin-Key 调用；公网匿名创建一律拒绝）
    _require_admin(request)
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


# ── 交易鉴权（v1.3.0 安全加固）：登录生成 token，交易端点必须带 Bearer token ──
# 修复前：/orders /portfolio /fund-accounts 仅凭请求体 username 识别身份，
# 公网可冒名操作任意账户。现改为：token → {username, role} 内存映射，
# 交易端点校验 Authorization: Bearer <token>，且身份必须匹配（admin 可代操作）。
TOKENS: dict = {}


def _issue_token(username: str, role: str) -> str:
    token = str(uuid.uuid4()) + str(uuid.uuid4())[:8]
    TOKENS[token] = {"username": username, "role": role}
    return token


def _require_auth(authorization: str = Header(default=""), username: str = Header(default="", alias="X-Username")):
    """校验 Bearer token；返回 token 对应的用户身份。
    兼容前端两种传法：Authorization: Bearer <t> 或 X-Username 头。
    """
    token = ""
    if authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):].strip()
    session = TOKENS.get(token)
    if not session:
        raise HTTPException(401, "未登录或登录已失效，请重新登录")
    return session




def _stock_fund_call(username: str, side: str, amount: float, idem_key: str):
    """调用 gipfel-api 资金桥（跨库：区域账户扣/加/查询）。失败抛 HTTPException。
    side: buy(扣) / sell(加) / query(查余额，amount/idem 忽略)"""
    import urllib.request
    import urllib.error
    GIPFEL_API = os.environ.get("GIPFEL_API_URL", "http://127.0.0.1:8000")
    if side == "query":
        from urllib.parse import quote as _urlquote
        url = f"{GIPFEL_API}/api/stock/fund?username={_urlquote(username)}"
        req = urllib.request.Request(url, method="GET")
    else:
        payload = json.dumps({
            "username": username, "side": side, "amount": round(amount, 2),
            "idempotency_key": idem_key,
        }).encode()
        req = urllib.request.Request(
            f"{GIPFEL_API}/api/stock/fund",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    # 内部密钥（与 gipfel-api 的 ADMIN_KEY 同源，本机回环鉴权）
    req.add_header("X-Internal-Key", os.environ.get("ADMIN_KEY", "gipfel-admin-dev"))
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("detail", "")
        except Exception:
            detail = ""
        if e.code == 400:
            raise HTTPException(400, detail or "资金不足或参数错误")
        raise HTTPException(502, f"资金服务暂不可用（{e.code}），请稍后重试")
    except Exception:
        raise HTTPException(502, "资金服务暂不可用，请稍后重试")


def _stock_fund_rollback(username: str, side: str, amount: float, idem_key: str):
    """补偿：买入失败回加 / 卖出失败回扣（幂等 key 复用，不重复执行）。"""
    try:
        reverse = "sell" if side == "buy" else "buy"
        _stock_fund_call(username, reverse, amount, f"{idem_key}-rollback")
    except Exception:
        # 补偿失败记录到日志（对账脚本可发现）；不阻断主流程返回错误
        print(f"[stock-fund-rollback-failed] username={username} side={side} amount={amount} key={idem_key}")



def _sync_company_account(db, g_user, g_token=""):
    """v1.3.0 公司级资金：登录时同步用户所属公司并确保公司账户存在。
    v1.3.1 多公司：优先取 g_user.company_ids（公司 id 列表，取第一个）；
    org_id（组织 id）仅作单值回退。返回 (company_id, company_name) 或 (None, None)。"""
    # ── 多公司优先：company_ids 是 companies.id 列表 ──
    company_ids = g_user.get("company_ids") or []
    if isinstance(company_ids, list) and len(company_ids) > 0:
        cid = int(company_ids[0])
        try:
            import urllib.request
            GIPFEL_API = os.environ.get("GIPFEL_API_URL", "http://127.0.0.1:8000")
            headers = {}
            if g_token:
                headers["Authorization"] = f"Bearer {g_token}"
            else:
                headers["X-Internal-Key"] = os.environ.get("ADMIN_KEY", "gipfel-admin-dev")
            req = urllib.request.Request(
                f"{GIPFEL_API}/api/companies/{cid}",
                headers=headers,
                method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                company = json.loads(resp.read().decode())
            cname = company.get("name") or ""
        except Exception:
            cname = ""
        # 确保公司账户存在（初始 10 万）
        db.execute("INSERT OR IGNORE INTO company_accounts(company_id, company_name, balance) VALUES(?,?,100000)",
                   (cid, cname))
        db.execute("UPDATE company_accounts SET company_name=? WHERE company_id=?", (cname, cid))
        db.commit()
        return cid, cname
    # ── 单值回退：org_id（组织 id → 公司）──
    org_id = g_user.get("org_id")
    if not org_id:
        return None, None
    try:
        import urllib.request
        GIPFEL_API = os.environ.get("GIPFEL_API_URL", "http://127.0.0.1:8000")
        headers = {}
        if g_token:
            headers["Authorization"] = f"Bearer {g_token}"
        else:
            headers["X-Internal-Key"] = os.environ.get("ADMIN_KEY", "gipfel-admin-dev")
        req = urllib.request.Request(
            f"{GIPFEL_API}/api/companies?org_id={org_id}",
            headers=headers,
            method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            companies = json.loads(resp.read().decode())
    except Exception:
        return None, None
    company = None
    if isinstance(companies, list):
        company = next((c for c in companies if c.get("org_id") == org_id), None)
    elif isinstance(companies, dict):
        items = companies.get("items") or companies.get("data") or []
        company = next((c for c in items if c.get("org_id") == org_id), None)
    if not company:
        return None, None
    cid = company.get("id")
    cname = company.get("name") or ""
    if cid is None:
        return None, None
    # 确保公司账户存在（初始 10 万）
    db.execute("INSERT OR IGNORE INTO company_accounts(company_id, company_name, balance) VALUES(?,?,100000)",
               (cid, cname))
    db.execute("UPDATE company_accounts SET company_name=? WHERE company_id=?", (cname, cid))
    db.commit()
    return cid, cname


def _require_operator(username_arg: str, session: dict) -> str:
    """买卖操作权限：仅 admin/operator（主席）。rep 只读。"""
    username = _require_self(username_arg, session)
    if session.get("role") not in ("admin", "operator"):
        raise HTTPException(403, "无买卖权限（仅主席/管理员可操作股票，代表端只读）")
    return username

def _require_self(username_arg: str, session: dict) -> str:
    """请求体/查询参数中的 username 必须与 token 身份一致（admin 可代操作）。"""
    uname = (username_arg or "").strip()
    if not uname:
        return session["username"]
    if uname != session["username"] and session["role"] != "admin":
        raise HTTPException(403, "无权操作其他用户账户")
    return uname


@app.post("/auth/login")
async def auth_login(request: Request):
    """统一登录 v2：单一账号源 = Gipfel 管理系统（gipfel-api）。
    本机转发 gipfel-api /api/auth/login 验证（bcrypt），验证通过后
    本地 users 表仅维护交易数据（balance/持仓），不存密码——
    改密码/删用户/角色变更只操作软件端，股票端登录时实时跟随。
    """
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise HTTPException(400, "缺少用户名或密码")

    # ── 转发验证到 gipfel-api（本机回环，不暴露公网）──
    import urllib.request
    import urllib.error
    GIPFEL_API = os.environ.get("GIPFEL_API_URL", "http://127.0.0.1:8000")
    payload = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{GIPFEL_API}/api/auth/login",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            gipfel = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise HTTPException(401, "用户名或密码错误")
        raise HTTPException(502, f"账号服务暂不可用（{e.code}），请稍后重试")
    except Exception:
        raise HTTPException(502, "账号服务暂不可用，请稍后重试")

    g_user = gipfel.get("user") or {}
    g_role = g_user.get("role") or "user"
    g_token = gipfel.get("token") or ""

    # ── 本地 upsert：仅交易数据（密码列存空，不再自管）──
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        db.execute("INSERT INTO users(username,password,balance,role) VALUES(?,?,100000,?)",
                   (username, "", g_role))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    elif user["role"] != g_role:
        # 角色变更实时跟随软件端
        db.execute("UPDATE users SET role=? WHERE id=?", (g_role, user["id"]))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    # ── v1.3.0 公司级资金：同步用户归属公司 + 确保公司账户存在 ──
    cid, cname = _sync_company_account(db, g_user, g_token)
    if cid is not None:
        db.execute("UPDATE users SET company_id=? WHERE id=?", (cid, user["id"]))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    db.close()

    u = dict(user)
    u.pop("password", None)  # 安全：绝不向客户端返回本地密码列（统一账号后恒为空，防御性移除）
    u["gipfel_token"] = g_token  # 一并返回软件端 token，前端可透传
    return {"token": _issue_token(username, u.get("role", "user")), "user": u}


@app.post("/auth/register")
async def auth_register(request: Request):
    """注册已关闭：账号统一由 Gipfel 管理系统创建（单一账号源）。
    保留端点返回明确提示，避免客户端误解。
    """
    raise HTTPException(403, "账号注册已关闭，请由系统管理员在 Gipfel 管理系统创建")



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
    """所有公司股票账户总览（v1.3.0 公司级：每公司资金 + 持仓市值 + 订单数）"""
    _require_admin(request)
    db = get_db()
    accounts = db.execute(
        "SELECT company_id, company_name, balance FROM company_accounts ORDER BY company_id").fetchall()
    positions = db.execute(
        "SELECT p.company_id, p.symbol, p.quantity, p.avg_price, s.current_price "
        "FROM portfolios p LEFT JOIN stocks s ON p.symbol = s.symbol"
    ).fetchall()
    orders = db.execute(
        "SELECT company_id, COUNT(*) AS cnt, "
        "SUM(CASE WHEN side='buy' THEN quantity ELSE 0 END) AS buy_qty, "
        "SUM(CASE WHEN side='sell' THEN quantity ELSE 0 END) AS sell_qty "
        "FROM orders GROUP BY company_id"
    ).fetchall()
    users_by_company = db.execute(
        "SELECT company_id, COUNT(*) AS cnt FROM users WHERE company_id IS NOT NULL GROUP BY company_id"
    ).fetchall()
    db.close()
    pos_map: dict = {}
    for p in positions:
        pos_map.setdefault(p["company_id"], []).append(dict(p))
    order_map: dict = {o["company_id"]: dict(o) for o in orders}
    user_map: dict = {r["company_id"]: r["cnt"] for r in users_by_company}
    result = []
    for acct in accounts:
        cid = acct["company_id"]
        pos = pos_map.get(cid, [])
        market_value = sum(
            (p["current_price"] or p["avg_price"] or 0) * p["quantity"] for p in pos
        )
        cash = float(acct["balance"] or 0)
        result.append({
            "id": cid,
            "company_id": cid,
            "company_name": acct["company_name"],
            "user_count": user_map.get(cid, 0),
            "balance": cash,
            "position_count": len(pos),
            "market_value": round(market_value, 2),
            "total_assets": round(cash + market_value, 2),
            "orders": order_map.get(cid, {"cnt": 0, "buy_qty": 0, "sell_qty": 0}),
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
    u = dict(user)
    u.pop("password", None)  # 安全：绝不向客户端返回本地密码列
    return {
        "user": u,
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


# ══════════════════════════════════════════════════════════════════
# 交易端点（v2 新增：补全买卖/持仓/资金账户——此前线上只跑精简版，
# 前端调用 /orders /portfolio /fund-accounts 全部 404，交易功能不可用）
# 设计：复用现有 users/orders/portfolios/stocks 表 + 原子扣款，
# 无 token 鉴权（与 /market 一致，身份由请求体 username 指定）
# ══════════════════════════════════════════════════════════════════


@app.post("/orders")
async def place_order(request: Request, session: dict = Depends(_require_auth)):
    """下单：买/卖（公司级资金账户 + 主席权限）
    v1.3.0 公司级改造：资金从本公司股票账户（company_accounts）扣/加，
    与区域基建账户完全分开；订单/持仓记公司维度（同公司代表只读可见）。
    权限：仅 admin/operator（主席）可买卖；rep 只读 403。
    buy：先扣公司账户款 → 写持仓/订单（失败补偿回加）
    sell：先加款到公司账户（失败不动持仓）→ 减持仓/订单"""
    data = await request.json()
    username = _require_operator(data.get("username"), session)
    symbol = (data.get("symbol") or "").upper().strip()
    side = (data.get("side") or "").strip().lower()
    idem_key = (data.get("idempotency_key") or "").strip()
    try:
        price = float(data.get("price") or 0)
        quantity = int(data.get("shares") or data.get("quantity") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "价格或数量格式错误")
    if side not in ("buy", "sell"):
        raise HTTPException(400, "side 仅支持 buy/sell")
    if price <= 0 or quantity <= 0:
        raise HTTPException(400, "价格和数量必须为正数")

    # ── v1.3.1 幂等：同 idempotency_key 已成交 → 直接返回原结果（防前端重提交双扣）──
    if idem_key:
        db0 = get_db()
        dup = db0.execute("SELECT * FROM orders WHERE idempotency_key=? AND status='filled'",
                          (idem_key,)).fetchone()
        if dup:
            db0.close()
            return {"accepted": True, "reason": "idempotent", "detail": "重复请求已忽略（幂等）",
                    "matched": dup["quantity"], "round": 0, "order": {
                        "username": username, "symbol": dup["symbol"], "side": dup["side"],
                        "price": dup["price"], "shares": dup["quantity"]}}
        db0.close()

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        db.close()
        raise HTTPException(404, "用户不存在，请先登录")
    stock = db.execute("SELECT * FROM stocks WHERE symbol=?", (symbol,)).fetchone()
    if not stock:
        db.close()
        raise HTTPException(404, f"股票 {symbol} 不存在")

    # ── 公司维度：主席操作的是本公司股票账户 ──
    cid = user["company_id"]
    if not cid:
        db.close()
        raise HTTPException(400, "当前账号未绑定公司，无法买卖股票")
    acct = db.execute("SELECT * FROM company_accounts WHERE company_id=?", (cid,)).fetchone()
    if not acct:
        db.close()
        raise HTTPException(400, "公司股票账户不存在，请重新登录")
    uid = user["id"]
    cost = round(price * quantity, 2)

    if side == "buy":
        # ① 原子扣公司账户款 + 防透支（同区域账户机制）
        cur = db.execute("UPDATE company_accounts SET balance = balance - ? WHERE company_id = ? AND balance >= ?",
                         (cost, cid, cost))
        if cur.rowcount == 0:
            db.close()
            raise HTTPException(400, f"公司可用资金不足：当前 {(acct['balance'] or 0):.2f}，需 ¥{cost:.2f}")
        try:
            # ② 写持仓（公司维度）+ 订单
            pos = db.execute("SELECT * FROM portfolios WHERE company_id=? AND symbol=?", (cid, symbol)).fetchone()
            if pos:
                old_qty = pos["quantity"] or 0
                old_avg = pos["avg_price"] or 0
                new_qty = old_qty + quantity
                new_avg = round((old_qty * old_avg + cost) / new_qty, 4)
                db.execute("UPDATE portfolios SET quantity=?, avg_price=? WHERE company_id=? AND symbol=?",
                           (new_qty, new_avg, cid, symbol))
            else:
                db.execute("INSERT INTO portfolios(company_id,user_id,symbol,quantity,avg_price) VALUES(?,?,?,?,?)",
                           (cid, uid, symbol, quantity, price))
            db.execute("INSERT INTO orders(company_id,user_id,symbol,side,quantity,price,status,idempotency_key) VALUES(?,?,?,?,?,?,'filled',?)",
                       (cid, uid, symbol, side, quantity, price, idem_key))
            db.commit()
        except Exception:
            # ③ 本地失败 → 补偿回加公司账户
            db.rollback()
            db.close()
            db2 = get_db()
            db2.execute("UPDATE company_accounts SET balance = balance + ? WHERE company_id = ?", (cost, cid))
            db2.commit()
            db2.close()
            raise HTTPException(502, "下单失败：持仓记录写入异常，资金已退回")
        new_bal = db.execute("SELECT balance FROM company_accounts WHERE company_id=?", (cid,)).fetchone()["balance"]
        db.close()
        return {"accepted": True, "reason": "", "detail": "买入成功", "matched": quantity,
                "round": 0, "balance": new_bal,
                "order": {"username": username, "symbol": symbol, "side": side,
                          "price": price, "shares": quantity}}
    else:  # sell
        pos = db.execute("SELECT * FROM portfolios WHERE company_id=? AND symbol=?", (cid, symbol)).fetchone()
        if not pos or (pos["quantity"] or 0) < quantity:
            db.close()
            raise HTTPException(400, f"持仓不足：当前 {pos['quantity'] if pos else 0} 股")
        # ① 先加款到公司账户（失败则不动持仓）
        db.execute("UPDATE company_accounts SET balance = balance + ? WHERE company_id = ?", (cost, cid))
        try:
            # ② 减持仓 + 订单
            new_qty = (pos["quantity"] or 0) - quantity
            if new_qty == 0:
                db.execute("DELETE FROM portfolios WHERE company_id=? AND symbol=?", (cid, symbol))
            else:
                db.execute("UPDATE portfolios SET quantity=? WHERE company_id=? AND symbol=?",
                           (new_qty, cid, symbol))
            db.execute("INSERT INTO orders(company_id,user_id,symbol,side,quantity,price,status,idempotency_key) VALUES(?,?,?,?,?,?,'filled',?)",
                       (cid, uid, symbol, side, quantity, price, idem_key))
            db.commit()
        except Exception:
            db.rollback()
            db.close()
            db2 = get_db()
            db2.execute("UPDATE company_accounts SET balance = balance - ? WHERE company_id = ?", (cost, cid))
            db2.commit()
            db2.close()
            raise HTTPException(502, "下单失败：持仓记录写入异常，资金已退回")
        db.close()
        db3 = get_db()
        sell_bal = db3.execute("SELECT balance FROM company_accounts WHERE company_id=?", (cid,)).fetchone()
        db3.close()
        return {"accepted": True, "reason": "", "detail": "卖出成功", "matched": quantity,
                "round": 0, "balance": sell_bal["balance"] if sell_bal else 0,
                "order": {"username": username, "symbol": symbol, "side": side,
                          "price": price, "shares": quantity}}


@app.get("/portfolio")
async def get_portfolio(request: Request, session: dict = Depends(_require_auth)):
    """持仓总览（对齐 Trading Arena 前端契约）
    v1.3.0 公司级：现金=本公司股票账户余额，持仓=公司维度（同公司代表可见）"""
    username = _require_self(request.query_params.get("username"), session)
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        db.close()
        raise HTTPException(404, "用户不存在")
    # 现金 = 公司股票账户余额（与区域基建账户分开）
    cid = user["company_id"]
    acct = db.execute("SELECT * FROM company_accounts WHERE company_id=?", (cid,)).fetchone() if cid else None
    cash = float(acct["balance"] or 0) if acct else 0.0
    stocks = {r["symbol"]: dict(r) for r in db.execute("SELECT * FROM stocks WHERE is_active=1").fetchall()}
    positions_raw = db.execute(
        "SELECT p.symbol, p.quantity, p.avg_price, s.name, s.current_price "
        "FROM portfolios p LEFT JOIN stocks s ON p.symbol=s.symbol WHERE p.company_id=? AND p.quantity>0",
        (cid,)).fetchall() if cid else []
    positions = []
    total_mv = 0.0
    total_cost = 0.0
    for row in positions_raw:
        symbol = row["symbol"]
        shares = float(row["quantity"] or 0)
        avg_cost = float(row["avg_price"] or 0)
        cur_price = float(row["current_price"] or avg_cost)
        name = row["name"] or symbol
        mv = round(cur_price * shares, 2)
        pnl = round(mv - avg_cost * shares, 2)
        total_mv += mv
        total_cost += avg_cost * shares
        positions.append({
            "symbol": symbol, "name": name, "shares": int(shares),
            "avgCost": round(avg_cost, 2), "currentPrice": round(cur_price, 2),
            "marketValue": mv, "pnl": pnl,
            "pnlRatio": round(pnl / (avg_cost * shares) * 100, 2) if avg_cost and shares else 0,
        })
    orders = [dict(r) for r in db.execute(
        "SELECT o.*, u.username FROM orders o JOIN users u ON o.user_id=u.id WHERE o.user_id=? ORDER BY o.id DESC LIMIT 20",
        (user["id"],)).fetchall()]
    db.close()
    total_pnl = round(total_mv - total_cost, 2)
    return {
        "user": {"username": username, "role": user["role"], "balance": cash},
        "summary": {
            "marketValue": round(total_mv, 2),
            "totalAssets": round(cash + total_mv, 2),
            "totalPnl": total_pnl,
            "pnlRatio": round(total_pnl / total_cost * 100, 2) if total_cost else 0,
        },
        "positions": positions,
        "orders": orders,
        "recentTrades": orders,
    }


@app.get("/fund-accounts")
async def fund_accounts(request: Request, session: dict = Depends(_require_auth)):
    """资金账户列表（v1.3.0 公司级：主资金账户 = 本公司股票账户余额，与区域基建分开）"""
    username = _require_self(request.query_params.get("username"), session)
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        db.close()
        raise HTTPException(404, "用户不存在")
    cid = user["company_id"]
    acct = db.execute("SELECT * FROM company_accounts WHERE company_id=?", (cid,)).fetchone() if cid else None
    cash = float(acct["balance"] or 0) if acct else 0.0
    acct_id = acct["company_id"] if acct else 0
    result = [{
        "id": acct_id, "name": "公司股票资金账户", "balance": cash,
        "initialBalance": cash, "locked": False, "symbol": str(acct_id),
    }]
    db.close()
    return result


@app.post("/fund-accounts")
async def create_fund_account(request: Request, session: dict = Depends(_require_auth)):
    """创建资金账户（v1.3.0 公司级：单账户模式，仅主席可调用；
    initial_balance 忽略——资金只经买卖流转，防代表端注入资金）"""
    data = await request.json()
    username = _require_operator(data.get("username"), session)  # 仅主席/管理员
    name = (data.get("name") or "主资金账户").strip()
    # amount 故意不使用：资金账户余额由买卖决定，不接受客户端注入
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        db.close()
        raise HTTPException(404, "用户不存在")
    # v1.3.0 公司级：余额 = 公司股票账户（与区域基建分开）——查询须在 close 前
    cid = user["company_id"]
    acct = db.execute("SELECT * FROM company_accounts WHERE company_id=?", (cid,)).fetchone() if cid else None
    cash = float(acct["balance"] or 0) if acct else 0.0
    acct_id = acct["company_id"] if acct else 0
    db.close()
    return {"accepted": True, "id": acct_id, "symbol": str(acct_id), "name": name,
            "balance": cash, "fundsLocked": False}


@app.delete("/fund-accounts/{account_id}")
async def delete_fund_account(account_id: int, request: Request, session: dict = Depends(_require_auth)):
    """删除资金账户（主账户不可删）"""
    raise HTTPException(400, "主资金账户不可删除")


@app.get("/available-companies")
async def available_companies():
    """可选公司（简化：返回空——公司绑定由 Gipfel 管理端维护）"""
    return []


@app.get("/my-companies")
async def my_companies(request: Request):
    """我的公司（简化：返回空）"""
    return []

