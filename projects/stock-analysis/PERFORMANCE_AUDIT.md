# stock-analysis 性能审计报告

**日期**: 2026-08-07
**审查范围**: 全部 Python 源文件 (app.py, api/main.py, api/db.py, api/trading.py, api/market_ops.py)

---

## 1. N+1 查询模式 — 🔴 严重

### 1.1 `api/trading.py` `_match_buy()` L54-61 — while 循环逐条查询 order_book

**文件**: `api/trading.py:54-61`
**严重程度**: 🔴 严重 — 每成交一笔多做一轮 DB 往返

```python
while remaining > 0:
    sell_order = row_dict(fetchone(conn, """
        SELECT id,username,price,shares
        FROM order_book
        WHERE stock_symbol=? AND trade_type='sell'
        ORDER BY price ASC,id ASC
        LIMIT 1
    """, (symbol,)))
```

**问题**: 每次 while 迭代都执行一次 `fetchone`，若卖单队列有 100 条则执行 100 次查询。同时每次还额外查询 `get_holding_shares` (L67) 和 pending_sell (L68-72)。

**影响**: 单笔买单在高活跃度市场中可能触发数十次 DB 查询。

**建议**: 改为一次 `fetchall` 取出所有匹配卖单，在内存中遍历匹配：

```python
sell_orders = fetchall(conn, """
    SELECT id,username,price,shares
    FROM order_book
    WHERE stock_symbol=? AND trade_type='sell'
    ORDER BY price ASC, id ASC
""", (symbol,))
for order in sell_orders:
    if remaining <= 0: break
    fill = min(remaining, order["shares"])
    ...
```

### 1.2 `api/trading.py` `_match_sell()` L112-118 — 同样的 while 循环逐条查询

**文件**: `api/trading.py:112-118`
**严重程度**: 🔴 严重

与 `_match_buy` 完全对称的问题，while 循环逐条取买单。

**建议**: 同上，一次 fetchall 后在内存处理。

### 1.3 `app.py` `_match_buy()` L490-491 / `_match_sell()` L529-530 — 同样的 while 循环 N+1

**文件**: `app.py:490-491`, `app.py:529-530`
**严重程度**: 🔴 严重

Streamlit 版本有相同的 N+1 循环。

### 1.4 `api/market_ops.py` `close_market()` L66-131 — 逐股票循环查询 buys/sells

**文件**: `api/market_ops.py:66-131`
**严重程度**: 🟠 高

```python
for stock in fetchall(conn, "SELECT symbol,current_price FROM stocks WHERE is_deleted=0"):
    buys = fetchall(conn, "SELECT ... FROM order_book WHERE stock_symbol=? ...", (symbol,))
    sells = fetchall(conn, "SELECT ... FROM order_book WHERE stock_symbol=? ...", (symbol,))
```

若有 20 只股票，则至少 40 次额外查询。此外 L111-131 对每个 buyer 还查一次 `users` 表 (L115)。

**建议**: 一次查询所有 order_book：

```python
all_orders = fetchall(conn,
    "SELECT * FROM order_book WHERE stock_symbol IN (SELECT symbol FROM stocks WHERE is_deleted=0)")
# 按 stock_symbol 分组后在内存处理
```

### 1.5 `api/main.py` `admin_fund_accounts()` L1072-1115 — 每个账户循环 3 次子查询

**文件**: `api/main.py:1072-1115`
**严重程度**: 🟠 高

对每个 fund_account 执行 `buys` 和 `sells` 查询（L1076-1083），且每个账户需要查 2 次（加上 mojibake 变体）。若 50 个账户 → 100 次查询。

**建议**: 用 `WHERE username IN (...)` 一次拉取所有账户的 buys/sells，在内存分组。

### 1.6 `api/market_ops.py` `rebuild_balances_before_round()` L211-226

**文件**: `api/market_ops.py:211-226`
**严重程度**: 🟠 高

循环中逐条 UPDATE users/fund_accounts 余额。交易记录数百/数千条时产生大量 DB 写操作。

**建议**: 先用内存聚合，再批量 UPDATE（或者直接 SQL SUM + UPDATE）。

---

## 2. 缓存策略 — 🟠 高

### 2.1 READ_CACHE 无容量上限 — 内存泄漏风险

**文件**: `api/main.py:32-33`
**严重程度**: 🟠 高

```python
READ_CACHE: dict[str, tuple[float, Any]] = {}
```

**问题**:
- 无容量上限（无 LRU/LFU 淘汰机制）
- 使用 `float` TTL + `time.monotonic()` — 过期 key 只在下次 `cache_get` 时被惰性删除
- `cache_get_or_set` 对每个 symbol 的 kline 都缓存 (`kline:{SYMBOL}`)，若股票数增长到数百只，缓存条目也等比例增长
- `portfolio:{username}` 缓存也随用户数线性增长
- 缓存值包含完整响应 JSON（含 positions/orders/recentTrades），单个用户可达数 KB

**建议**:
- 使用 `lru_cache` 或引入 LRU 淘汰（`cachetools.TTLCache` 或 `functools.lru_cache`）
- 设置最大条目数（建议 500-1000）
- 考虑使用 Redis 替代进程内存（解决多 worker 不共享问题）

### 2.2 TTL 设置过短

**文件**: `api/main.py:576` (market: 2s), `api/main.py:619` (kline: 2s), `api/main.py:640` (portfolio: 3s), `api/main.py:1117` (admin_fund_accounts: 30s)

- market 和 kline 的 2s TTL 在低频查询场景下几乎等于没有缓存
- portfolio 的 3s TTL 合理
- admin_fund_accounts 30s 合理但查询本身就慢（N+1）

**建议**: market/kline 至少 5-10s，配合 `stale-while-revalidate` 头提供 CDN 缓存。

### 2.3 缓存键设计无命名空间隔离

**文件**: `api/main.py:32,70`

`cache_get_or_set("market", ...)` 在所有请求间共享。由于写操作后调用 `clear_read_cache()` 清空整个缓存，容易造成缓存击穿（写操作后短时间内大量读请求同时回源 DB）。

**建议**:
- 不要全量清除，改为按 key 精确失效
- 或使用写后主动刷新缓存（write-through）

---

## 3. 数据库连接池配置 — 🟡 中等

### 3.1 max_size=6 对多用户并发可能不足

**文件**: `api/db.py:35-41`
**严重程度**: 🟡 中等

```python
_pool = psycopg_pool.ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=6,
    open=True,
    timeout=10,
)
```

**问题**:
- max_size=6 意味着 PostgreSQL 下最多 6 个并发 DB 操作
- 配合 FastAPI（默认多 worker），每个 worker 的请求可能在等待连接
- timeout=10 秒 — 连接池耗尽后请求等待 10 秒才报错

**建议**:
- 增加 max_size 到 20-30（取决于 PostgreSQL `max_connections` 设置）
- 对于 SQLite 模式（无连接池），考虑添加 WAL 模式 + `timeout` 参数
- 添加连接池监控指标（pool size, wait time）

### 3.2 SQLite 模式无连接池

**文件**: `api/db.py:52-65`
**严重程度**: 🟡 中等

```python
def connect():
    if not is_postgres():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
```

每次 `with connect() as conn:` 都创建一个新的 sqlite3 连接。无连接复用，无 WAL 模式配置（仅 app.py L117 配置了）。

**建议**:
- 为 SQLite 也实现简单连接池（单连接 + check_same_thread=False）
- FastAPI 下统一设置 `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=NORMAL`

---

## 4. 阻塞 I/O — 🔴 严重

### 4.1 `app.py` `urllib.request.urlopen` — 同步 HTTP 阻塞

**文件**: `app.py:41-42`, `app.py:53`
**严重程度**: 🔴 严重

```python
import urllib.request, urllib.error

def github_api_request(url, ...):
    req = urllib.request.Request(url, ...)
    with urllib.request.urlopen(req, timeout=20) as resp:  # 同步阻塞!
```

```python
with urllib.request.urlopen(backup_url, timeout=30) as resp:  # 同步阻塞!
```

**问题**:
- `urllib.request.urlopen` 是同步调用，直接阻塞 Streamlit 的事件循环
- timeout=20/30 秒 — 在网络故障时 UI 冻结 20-30 秒
- `restore_db_from_backup_if_missing()` 在数据库不存在时启动即调用，堵塞整个启动流程

**影响**: Streamlit 是单线程的，任何同步 I/O 都会冻结整个 UI。

**建议**:
- 使用 `urllib.request` 配合 `concurrent.futures.ThreadPoolExecutor`
- 或换用 `httpx`（支持 async）
- 对于 Streamlit 场景：使用 `@st.cache_data` + 后台线程加载

### 4.2 `persist_db_backup()` 写入整个 DB 文件到 GitHub

**文件**: `app.py:72-104`
**严重程度**: 🟠 高

每次 close_market 后，将整个 SQLite DB 文件 base64 编码后通过 GitHub API 上传。操作阻塞直到 GitHub 响应。

**建议**:
- 改为后台线程异步上传
- 考虑增量备份而非全量上传

---

## 5. 大文件/数据加载 — 🟡 中等

### 5.1 `app.py` 194KB / 3645 行单体文件

**文件**: `app.py` — 3645 行, 194KB
**严重程度**: 🟡 中等

**问题**:
- 每次 Streamlit 页面加载，Python 需要解析整个 194KB 文件
- 包含所有 DB 查询逻辑、UI 渲染、K 线计算、交易撮合、管理员功能在一个文件中
- 多个函数重复出现在 app.py 和 api/ 中（`_match_buy`, `_match_sell`, `close_market`, `open_market`, `compute_price` 等）
- 代码重复：`app.py` L485-557 (`_match_buy`/`_match_sell`) 与 `api/trading.py` L50-161 几乎重复

**建议**:
- 拆分模块：`app_pages/` (Streamlit pages), `db_ops.py` (共享 DB 操作), `market_engine.py` (交易撮合引擎)
- 消除 app.py 和 api/ 之间的代码重复（撮合逻辑只保留一份）
- 使用 `Streamlit` multipage 特性 (pages/ 目录)

### 5.2 `api/main.py` 1375 行 — 也可拆分

**文件**: `api/main.py` — 1375 行, 61KB
**严重程度**: 🟢 低

虽然比 app.py 好很多，但路由、认证、模型定义、工具函数混在一起。建议拆分为 `api/routes/`, `api/auth.py`, `api/models.py`。

---

## 6. 索引缺失 — 🔴 严重

### 6.1 全局只有 1 个手动创建的非主键索引

**发现**: 整个项目中仅 `api/main.py:254-255` 创建了 2 个索引（均针对 fund_accounts）：

```python
CREATE INDEX IF NOT EXISTS idx_fund_accounts_owner ON fund_accounts(owner)
CREATE UNIQUE INDEX IF NOT EXISTS idx_fund_accounts_owner_unique ON fund_accounts(owner, name)
```

### 6.2 缺失的关键索引

| 表 | 高频查询列 | 查询出现位置 | 严重程度 |
|---|---|---|---|
| `order_book` | `(stock_symbol, trade_type)` | trading.py L54-61, L112-118 | 🔴 严重 |
| `order_book` | `(username, stock_symbol, trade_type)` | trading.py L68-72, L242-246 | 🔴 严重 |
| `transactions` | `(username, stock_symbol)` | trading.py L19-27 (get_holding_shares) | 🔴 严重 |
| `transactions` | `(round)` | market_ops.py L246-247 (rollback) | 🟠 高 |
| `kline` | `(stock_symbol, round)` | main.py L585-592 | 🟠 高 |
| `stocks` | `(is_deleted)` | 几乎每处 stocks 查询都用 | 🟡 中等 |
| `rounds` | `(stock_symbol, is_settled)` | market_ops.py L140, main.py L196-197 | 🟠 高 |
| `audit_logs` | `(id)` | main.py L1349 | 🟢 低 (PK 已覆盖) |

**影响**:
- `order_book` 按 `stock_symbol + trade_type` 排序查询是全表扫描
- `transactions` 按 `username + stock_symbol` 计算持仓是全表扫描
- 多股票并发交易时，每个撮合请求都触发全表扫描

**建议**:

```sql
-- 最关键：order_book 撮合查询
CREATE INDEX idx_order_book_symbol_type_price ON order_book(stock_symbol, trade_type, price, id);

-- 交易记录：持仓查询
CREATE INDEX idx_transactions_user_symbol ON transactions(username, stock_symbol);
CREATE INDEX idx_transactions_round ON transactions(round);

-- K线查询
CREATE INDEX idx_kline_symbol_round ON kline(stock_symbol, round);

-- stocks 常用过滤
CREATE INDEX idx_stocks_not_deleted ON stocks(symbol) WHERE is_deleted=0;

-- rounds 未结算查询
CREATE INDEX idx_rounds_symbol_settled ON rounds(stock_symbol, is_settled);
```

---

## 7. 其他发现问题

### 7.1 速率限制用内存字典 — 多 worker 不共享

**文件**: `api/main.py:119-121`
**严重程度**: 🟡 中等

```python
_rate_buckets: dict[str, list[float]] = defaultdict(list)
```

**问题**:
- FastAPI 多 worker (uvicorn --workers 4) 时每个 worker 有独立的 `_rate_buckets`
- 限流在 worker 之间不共享，用户切换 worker 可绕过
- 无限增长：旧 IP 的 bucket 永远不会被清理（L128-129 只清理时间戳列表，不清理 key）

**建议**:
- 使用 Redis 实现分布式速率限制
- 或至少定期清理过期 IP 条目

### 7.2 `current_user` 依赖每次验证 token 都查 DB

**文件**: `api/main.py:330-341`
**严重程度**: 🟢 低

每次 API 调用都从 DB 查询用户信息（L335-338）。可以考虑为已验证用户添加短期内存缓存（如 60s TTL）。

### 7.3 `compute_price` 每次结算重复计算

**文件**: `api/market_ops.py:48-56`, `app.py` 重复定义
**严重程度**: 🟢 低

算法在两个文件中重复，且计算逻辑固定。

---

## 优先级排序

| 优先级 | 问题 | 改动成本 | 影响范围 |
|---|---|---|---|
| **P0** | 索引缺失 (#6) | 低 — 纯 DDL | 所有查询性能提升 10-100x |
| **P0** | `_match_buy`/`_match_sell` N+1 (#1.1, #1.2) | 低 — 改 while 为 fetchall | 交易延迟降低 10-50x |
| **P0** | `urllib.request` 同步阻塞 (#4.1) | 低 — 加线程池 | 消除 UI 冻结 |
| **P1** | READ_CACHE 无上限 (#2.1) | 中 — 引入 cachetools | 防止 OOM |
| **P1** | `close_market` N+1 (#1.4) | 中 — 重构结算逻辑 | 降低结算耗时 |
| **P1** | `admin_fund_accounts` N+1 (#1.5) | 低 | 管理员页面加载 |
| **P2** | 连接池 max_size 偏小 (#3.1) | 低 — 改配置 | 高并发场景 |
| **P2** | 速率限制多 worker 不共享 (#7.1) | 中 — 需 Redis | 限流准确性 |
| **P3** | app.py 单体拆分 (#5.1) | 高 | 可维护性 |
| **P3** | 代码去重 (app.py ↔ api/) | 高 | 可维护性 |

---

## 总结

- **3 个 P0 问题**应立即修复：索引缺失、交易循环 N+1、同步 HTTP 阻塞
- **4 个 P1 问题**建议近期修复：缓存无上限、结算 N+1、admin N+1
- **2 个 P2+ 问题**可在后续迭代：连接池、架构重构
- 项目在低并发 (<10 用户) 时可勉强运行，但在 50+ 并发用户下交易撮合延迟将显著劣化
