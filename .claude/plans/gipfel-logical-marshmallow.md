# 并发性能加固

## 负载分析

200 人看面板 + 25 人交易时压力：

| 来源 | 请求频率 | 请求量 |
|------|----------|--------|
| 行情轮询 | 每 4s × 200 人 | ~100 req/s |
| K-line 轮询 | 每 4s × 200 人 | ~50 req/s |
| 资产刷新 | 每 4s × 25 人 | ~6 req/s (含 4-5 SQL/req) |
| 下单 | 突发 | 每单 ~10 SQL |

## 瓶颈与修复

### 1. 数据库连接池 — db.py
当前每请求新建 `psycopg.connect()`，25 个并发 + HTTP 连接池瞬间耗尽 Neon 免费版 20-50 连接。

**修复**：使用 `psycopg_pool.ConnectionPool`，应用启动时初始化，请求时 `pool.connection()`。

### 2. 速率限制 — main.py
无任何限流，200 人轮询 + 恶意刷新可直接打满。

**修复**：添加内存计数器中间件，读接口 60 req/min/IP，写接口 20 req/min/IP。

### 3. 下单并发锁 — trading.py
25 人同时买卖同一只股票，订单簿匹配无锁，会读脏数据或产生 PostgreSQL 串行化异常。

**修复**：`place_order` 内用 `pg_try_advisory_xact_lock(symbol_hash)` 实现逐只股票串行化。

### 4. 资产接口缓存 — main.py
`/portfolio` 每次 4-5 个 SQL 无缓存，25 人每 4s 就是 100+ SQL/s。

**修复**：加入 3 秒内存缓存，key 为 `portfolio:{username}`，下单成功后清除。

### 5. 数据库索引完善 — schema
关键查询缺索引。

**修复**：`transactions(username, stock_symbol)` 覆盖持仓查询；`rounds(is_settled)` 覆盖结算扫描。

### 6. 前端降频 — TradingWorkspace.tsx
两个 4s 定时器并行跑，200 人 = 150 req/s。

**修复**：行情轮询改为 6s，K-line 改为 10s，减少冗余请求。

## 文件清单

| 文件 | 改动 |
|------|------|
| `api/db.py` | 连接池、请求级 connection() 函数 |
| `api/requirements.txt` | 加 `psycopg-pool` |
| `api/main.py` | 速率限制中间件、portfolio 缓存 |
| `api/trading.py` | 股票级咨询锁 |
| `api/schema.postgres.sql` | 加 2 个索引 |
| `web/components/TradingWorkspace.tsx` | 轮询间隔 6s/10s |

## 验证

1. `python -m py_compile` 全部文件
2. 前端 build 检查：`cd web && npm run build`
3. 部署后确认健康检查和行情/订单接口正常
