# stock-analysis 安全审查报告

> **审查日期**: 2026-08-07  
> **审查范围**: `api/main.py` (1375行), `api/db.py` (81行), `api/trading.py` (252行), `app.py` (3645行)  
> **审查方法**: 静态代码分析 + 模式搜索  
> **风险等级**: 🔴 严重 · 🟠 高 · 🟡 中 · 🟢 低

---

## 🔴 严重 (CRITICAL) — 需立即修复

### C1. TOKEN_SECRET 硬编码默认值

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L22 | 🔴 严重 |

```python
# L22
TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "change-me-before-production")
```

**问题**: 默认 `TOKEN_SECRET` 字符串 `"change-me-before-production"` 直接硬编码在源码中。如果部署时忘记设置环境变量，所有 JWT Token 的 HMAC 签名使用该已知密钥，攻击者可伪造任意用户（含 admin）的 Token。

**修复建议**:
```python
TOKEN_SECRET = os.environ.get("TOKEN_SECRET")
if not TOKEN_SECRET:
    raise RuntimeError("TOKEN_SECRET environment variable is required")
```

---

### C2. ADMIN_PASSWORD 硬编码默认值 `admin123`

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L307 | 🔴 严重 |
| `app.py` | L280 | 🔴 严重 |

```python
# api/main.py L306-307
def admin_recovery_password() -> str:
    return os.environ.get("ADMIN_PASSWORD") or "admin123"

# app.py L280
admin_pw = get_admin_password() or "admin123"
```

**问题**: `admin123` 是极其常见的弱密码。两处都将其作为后备默认值。

**修复建议**: 移除默认值，缺失时拒绝启动或生成随机密码并打印到日志。

---

### C3. Admin 恢复密码绕过正常认证

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L364-377 | 🔴 严重 |

```python
if payload.username == "admin" and payload.password == admin_recovery_password() and (
    not user or not check_pwd(str(user["password"]), payload.password)
):
    # 直接用恢复密码创建/覆盖 admin 用户
    execute(conn, "UPDATE users SET password=?, role='admin', status='active' WHERE username='admin'", ...)
```

**问题**: 
1. 恢复密码 (`admin123` 默认) 可**无条件覆盖** admin 账户密码
2. 即使用户已修改 admin 密码，恢复密码仍可登录
3. 恢复密码比较使用 `==` 而非 `hmac.compare_digest`，但外层 `check_pwd` 失败条件已给出时序差异

**修复建议**: 
- 仅在 admin 账户不存在时（首次初始化）允许恢复密码创建
- 如果账户已存在，恢复密码不应覆盖现有密码
- 使用 `hmac.compare_digest` 比较恢复密码

---

## 🟠 高 (HIGH) — 需尽快修复

### H1. f-string SQL 拼接 — `_update_balance()`

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/trading.py` | L43, L46, L47 | 🟠 高 |

```python
def _update_balance(conn, trader: str, amount: float, direction: str) -> None:
    if trader.startswith(COMPANY_USER_PREFIX) and trader.endswith("]"):
        sym = trader[len(COMPANY_USER_PREFIX):-1]
        execute(conn, f"UPDATE stocks SET balance=balance{direction}? WHERE symbol=?", (amount, sym))
    if trader.startswith(ACCOUNT_USER_PREFIX) and trader.endswith("]"):
        account_id = int(trader[len(ACCOUNT_USER_PREFIX):-1])
        execute(conn, f"UPDATE fund_accounts SET balance=balance{direction}? WHERE id=?", (amount, account_id))
    execute(conn, f"UPDATE users SET balance=balance{direction}? WHERE username=?", (amount, trader))
```

**问题**: `direction` 参数来自调用方（`_update_balance(conn, username, amount, "-")`）且目前固定为 `"+"` 或 `"-"`，但使用 f-string 拼接 SQL 仍不符合安全编码规范。如果未来调用方传入不受控的值，就是 SQL 注入。

**修复建议**:
```python
def _update_balance(conn, trader: str, amount: float, direction: str) -> None:
    if direction not in ("+", "-"):
        raise ValueError("direction must be '+' or '-'")
    signed_amount = amount if direction == "+" else -amount
    if trader.startswith(COMPANY_USER_PREFIX) and trader.endswith("]"):
        sym = trader[len(COMPANY_USER_PREFIX):-1]
        execute(conn, "UPDATE stocks SET balance=balance+? WHERE symbol=?", (signed_amount, sym))
    # ... 同理处理其他表
```

---

### H2. f-string SQL 拼接 — admin_update_stock / app.py update_stock_params

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L1192-1198 | 🟠 高 |
| `app.py` | L464-466 | 🟠 高 |

```python
# api/main.py L1192-1198
sets = ", ".join(f"{column_map[key]}=?" for key in safe) + ", last_update=CURRENT_TIMESTAMP"
vals = tuple(safe.values()) + (symbol.upper(),)
execute(conn, f"UPDATE stocks SET {sets} WHERE symbol=?", vals)

# app.py L464-466
sets = ", ".join(f"{k}=?" for k in safe)
vals = list(safe.values()) + [sid]
conn.execute(f"UPDATE stocks SET {sets} WHERE id=?", vals)
```

**问题**: 列名来自 `column_map` 字典（api）或 `allowed` 白名单（app.py），虽然**当前**安全，但 f-string 拼 SQL 列名是不良模式。如果有人修改了白名单逻辑，可能引入注入。

**修复建议**: 当前有多重白名单保护（`column_map` + `payload.model_dump(exclude_none=True)`），风险可控。建议在列名上添加额外校验：
```python
if not all(re.match(r'^[a-z_]+$', column_map[key]) for key in safe):
    raise ValueError("invalid column name")
```

---

### H3. CORS allow_credentials=True + 潜在通配符来源

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L36-44, L83-86 | 🟠 高 |

```python
def cors_origins() -> list[str]:
    configured = {
        origin.strip()
        for origin in os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",")
        if origin.strip()
    }
    if "*" in configured:
        return ["*"]  # ← 问题
    return sorted(configured | DEFAULT_CORS_ORIGINS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,   # ← 与 "*" 不兼容
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**问题**: 当 `CORS_ALLOW_ORIGINS` 未设置时，默认值为 `"*"`，导致 `allow_origins=["*"]`。**`allow_credentials=True` 与 `allow_origins=["*"]` 同时使用违反 CORS 规范**，浏览器会拒绝请求。更严重的是，即使能工作，这允许任意网站携带凭证发起跨域请求。

**修复建议**: 默认禁止 credentials + wildcard 的组合。生产环境必须配置具体域名：
```python
def cors_origins() -> list[str]:
    configured_raw = os.environ.get("CORS_ALLOW_ORIGINS", "")
    if not configured_raw:
        return sorted(DEFAULT_CORS_ORIGINS)  # 生产默认使用已知域名
    ...
```

---

## 🟡 中 (MEDIUM) — 建议修复

### M1. `/health` 端点泄露内部配置

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L91-102, L344-355 | 🟡 中 |

```json
{
    "ok": true,
    "database": true,
    "backend": "postgres",
    "path": "",
    "tokenSecretConfigured": false,    // ← 泄露: TOKEN_SECRET 是否配置
    "orderWritesEnabled": false,       // ← 泄露: 写开关状态
    "marketWritesEnabled": false,
    "adminWritesEnabled": false
}
```

**问题**: `/health` 端点无需认证即可访问，暴露了：
1. 数据库路径（SQLite 模式）— 可能被用于路径遍历攻击
2. `tokenSecretConfigured`: 攻击者可据此判断是否使用默认 TOKEN_SECRET
3. 各功能开关状态

**修复建议**:
```python
@app.get("/health")
def health():
    return {"ok": True, "database": is_postgres() or DB_PATH.exists()}
```

---

### M2. Admin 端点绕过限速

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L138-141 | 🟡 中 |

```python
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/admin") or path in ("/", "/health"):
        return await call_next(request)  # ← 跳过限速
```

**问题**: 所有 `/admin/*` 端点完全绕过限速，包括 `/auth/login`（登录后 admin 操作）。虽然 admin 端点需要认证，但暴力破解 Token 或撞库仍不受限。

**修复建议**: 至少对 admin 端点应用较高的 write 限速（如每分钟 1000 次），而非完全不限。

---

### M3. 弱密码策略

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L179, L187, L192 | 🟡 中 |

```python
class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=120)
```

**问题**: 密码最小长度为 **1 字符**，允许空密码或极弱密码。结合 admin 恢复密码默认 `admin123`，整体密码安全性弱。

**修复建议**: 最小密码长度 8-12 字符，建议增加复杂度要求。

---

### M4. `bind()` 函数 ? → %s 替换存在误替换风险

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/db.py` | L48-49 | 🟡 中 |

```python
def bind(sql: str) -> str:
    return sql.replace("?", "%s") if is_postgres() else sql
```

**问题**: `str.replace("?", "%s")` 是盲目替换，如果 SQL 字符串字面量中包含 `?`（如 `WHERE name='what?'`），会被错误替换为 `%s`。

**修复建议**: 使用正则表达式仅在占位符位置替换，或使用 psycopg 的 `%s` 风格并改用 `pyformat` 参数。

---

### M5. app.py 的 `check_pwd()` 未使用恒定时间比较

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `app.py` | L135-140 | 🟡 中 |

```python
def check_pwd(stored, plain):
    if ":" in stored:
        salt, h = stored.split(":", 1)
        return hash_pwd(plain, salt) == h  # ← 非恒定时间比较
    return hash_pwd(plain) == stored
```

**问题**: 使用 `==` 而非 `hmac.compare_digest`，可能通过时序攻击推断密码哈希。相比 api/main.py 的 `check_pwd` (L299-303) 正确使用了 `hmac.compare_digest`，app.py 版本落后。

**修复建议**: 与 api/main.py 对齐，使用 `hmac.compare_digest`。

---

### M6. 密码哈希算法: SHA-256 而非专用密码哈希函数

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L290-291 | 🟡 中 |
| `app.py` | L128 | 🟡 中 |

```python
def hash_pwd(password: str, salt: str = "") -> str:
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()
```

**问题**: SHA-256 是通用哈希函数，不适合密码存储。应使用 bcrypt/scrypt/argon2 等专用密码哈希函数，它们内置盐值、可配置迭代次数、故意设计为慢速以抵抗暴力破解。

**修复建议**: 迁移到 `hashlib.scrypt()` 或引入 `bcrypt` 库。注意需要数据迁移方案。

---

## 🟢 低 (LOW) — 可择机修复

### L1. `pg_advisory_xact_lock` 使用 `hash(symbol)` 存在哈希碰撞风险

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/trading.py` | L220 | 🟢 低 |

```python
lock_id = hash(symbol) & 0x7FFFFFFF
execute(conn, "SELECT pg_advisory_xact_lock(%s)", (lock_id,))
```

**问题**: Python 的 `hash()` 在不同进程间不保证一致，且碰撞可能导致不相关股票互相阻塞。对于 4 只股票的场景实际上风险极低。

**修复建议**: 使用更确定的哈希（如 `int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF`）或直接用数据库行锁。

---

### L2. Token TTL 过长 (8小时)

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L23 | 🟢 低 |

```python
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "28800"))  # 8小时
```

**问题**: 8 小时的 Token 有效期较长。如果 Token 泄露，攻击窗口大。

**修复建议**: 生产环境建议 1-2 小时，配合 refresh token 机制。

---

### L3. Token 无 `jti` / 无法撤销

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L310-313, L383 | 🟢 低 |

**问题**: Token 签发后无法主动撤销（无黑名单/撤销列表），只能等待过期。

**修复建议**: 加入 `jti` (JWT ID) 并维护撤销列表，或在数据库中存 token 版本号。

---

### L4. 缺少安全响应头

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | — (缺失) | 🟢 低 |

**问题**: FastAPI 应用未设置安全相关 HTTP 头：
- 无 `X-Content-Type-Options: nosniff`
- 无 `X-Frame-Options: DENY`
- 无 `Content-Security-Policy`
- 无 `Strict-Transport-Security`

**修复建议**: 添加中间件注入安全头，或使用 `secure` 中间件。

---

### L5. `Rate limit for login is 60 per minute` — 登录限速较宽松

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L120 | 🟢 低 |

**问题**: 每分钟允许 60 次登录尝试。虽有限速但较宽松。

**修复建议**: 降低到 10-20 次/分钟，并结合账户级别的失败锁定。

---

### L6. `ensure_fund_accounts_schema` 使用 f-string 拼接 `id_type`

| 文件 | 行号 | 严重程度 |
|------|------|----------|
| `api/main.py` | L242-252 | 🟢 低 |

```python
id_type = "BIGSERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
execute(conn, f"""
    CREATE TABLE IF NOT EXISTS fund_accounts (
        id {id_type},
        ...
    )
""")
```

**问题**: `id_type` 来自 `is_postgres()` 布尔判断的固定值，非用户输入，实际风险极低。

---

## 📊 汇总统计

| 严重程度 | 数量 | 需立即处理 |
|----------|------|------------|
| 🔴 严重 | 3 | C1, C2, C3 |
| 🟠 高 | 3 | H1, H2, H3 |
| 🟡 中 | 6 | M1-M6 |
| 🟢 低 | 6 | L1-L6 |
| **合计** | **18** | |

---

## 🔥 优先修复清单 (Top 5)

1. **C1** — `TOKEN_SECRET` 移除默认值，强制环境变量
2. **C3** — 修复 admin 恢复密码绕过认证
3. **C2** — 移除 `admin123` 默认密码
4. **H3** — 修复 CORS `allow_credentials=True` + wildcard 冲突
5. **H1** — 消除 `_update_balance()` 中的 f-string SQL 拼接
