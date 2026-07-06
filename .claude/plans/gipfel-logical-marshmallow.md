# 并发性能保障 — 30人交易 + 150人看盘

## 已存在的保护机制

| 机制 | 位置 | 说明 |
|------|------|------|
| PostgreSQL 连接池 | `api/db.py:20-38` | min=1, max=10, timeout=5s |
| 读缓存 | `api/main.py:30-77` | market 2s、kline 2s、portfolio 3s |
| 速率限制 | `api/main.py:118-151` | 读 60000/IP/min、写 3000/IP/min |
| Advisory lock | `api/trading.py:219-221` | 每只股票独立锁，防并发下单冲突 |

## 需要修复的问题

### 1. 连接池初始化竞态 (高危)

`api/db.py:20-23` — `get_pool()` 没有锁，两个并发请求同时检测到 `_pool is None` 会各自创建一个连接池：

```python
def get_pool():
    global _pool
    if is_postgres() and _pool is None:  # ← 没有锁，竞态
        ...
        _pool = psycopg_pool.ConnectionPool(...)
```

修复：加 `threading.Lock` 保护。

### 2. 连接池耗尽风险 (中危)

max_size=10，30人同时下单 + 150人看盘，每个请求获取一个连接。看盘请求（market/kline）有2s缓存，大部分不开连接。但下单、持仓、管理页面直接连库。如果30人同时下单，可能等连接超时。

修复：提高 max_size 到 20，加连接健康检查。

### 3. 写请求无缓存穿透保护 (中危)

`/orders`、`/admin/market/*` 等写请求每次直接操作数据库。如果短时间内大量下单，数据库可能成为瓶颈。但 advisory lock 已经按股票串行化了。

## 改动清单

### `api/db.py` — 连接池安全

```python
_pool_lock = threading.Lock()

def get_pool():
    global _pool
    if not is_postgres():
        return None
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        import psycopg_pool
        from psycopg.rows import dict_row
        _pool = psycopg_pool.ConnectionPool(
            DATABASE_URL,
            min_size=2,
            max_size=20,
            open=True,
            timeout=10,
            kwargs={"row_factory": dict_row},
        )
    return _pool
```

### `api/main.py` — 健康检查增加连接池状态

在 `/health` 中返回 pool 使用情况，方便监控。

## 验证方式

1. 部署后 `/health` 正常返回
2. 多线程测试：同时发 30 个下单请求不报错
3. 并发读测试：同时发 150 个 market 请求响应正常
