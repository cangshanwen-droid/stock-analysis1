# 正式上线检查清单

## 1. 数据库

- 在 Neon 或 Supabase 创建 PostgreSQL 数据库。
- 执行 `api/schema.postgres.sql`。
- 从 Streamlit 当前 SQLite 迁移数据：

```bash
cd api
pip install -r requirements.txt
set DATABASE_URL=postgresql://user:password@host:5432/gipfel
set SQLITE_DB_PATH=../data/stock_analysis.db
python migrate_sqlite_to_postgres.py
```

验收：

- `users`、`stocks`、`market_state`、`kline`、`transactions`、`order_book`、`audit_logs` 都有数据。
- `market_state.round` 与比赛当前轮次一致。
- 管理员和选手账号能登录。

## 2. 后端 API

推荐先用 Render 部署：

- Blueprint 文件：`render.yaml`
- Dockerfile：`api/Dockerfile`
- 健康检查：`/health`

环境变量：

```text
DATABASE_URL=postgresql://...
CORS_ALLOW_ORIGINS=https://你的前端域名
TOKEN_SECRET=至少32位随机字符串
TOKEN_TTL_SECONDS=28800
ENABLE_ORDER_WRITES=false
ENABLE_MARKET_WRITES=false
ENABLE_ADMIN_WRITES=false
```

验收：

- `https://api.your-domain.com/health` 返回 `ok: true`
- `backend` 返回 `postgres`
- `tokenSecretConfigured` 返回 `true`
- 三个写入开关上线初期保持 `false`

## 3. 前端网页

推荐用 Vercel 部署 `web/` 目录：

- Root Directory：`web`
- Install Command：`npm ci`
- Build Command：`npm run build`

环境变量：

```text
NEXT_PUBLIC_API_BASE=https://api.your-domain.com
```

验收：

- 正式网页能打开行情面板。
- `player1/player1` 能登录并查看资产、委托、成交。
- `admin` 管理员能看到管理控制台。
- 电脑端侧边导航和移动端底部导航都能切换。

## 4. 开启真实写入

只在完成全流程测试后逐项开启：

```text
ENABLE_ORDER_WRITES=true
ENABLE_MARKET_WRITES=true
ENABLE_ADMIN_WRITES=true
```

开启顺序建议：

1. 先开启 `ENABLE_ORDER_WRITES`，测试选手下单、成交、撤单边界。
2. 再开启 `ENABLE_MARKET_WRITES`，测试收盘、开盘、轮次递增、K 线生成。
3. 最后开启 `ENABLE_ADMIN_WRITES`，测试用户状态、密码重置、股票参数更新。

## 5. 域名

推荐结构：

```text
https://gipfel.your-domain.com
https://api.gipfel.your-domain.com
```

Cloudflare DNS：

- 前端 CNAME 指向 Vercel。
- 后端 CNAME 指向 Render。
- 开启 HTTPS。
- API 的 CORS 只允许正式前端域名。

## 6. 备份和回滚

- PostgreSQL 每天自动备份。
- 比赛前导出一次完整数据库快照。
- 保留 Streamlit 旧入口作为临时兜底。
- 每次开启写入开关前先记录当前 Git commit 和数据库备份点。
