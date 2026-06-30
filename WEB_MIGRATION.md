# Gipfel 正式网页迁移方案

当前 Streamlit 版本继续保留，用作现网兜底。正式网页迁移采用渐进式方式，先把行情、交易、管理能力拆成独立前后端，再切换到持久化数据库和独立域名。

## 目标架构

- 前端：Next.js + TradingView Lightweight Charts
- 后端：FastAPI
- 数据库：PostgreSQL
- 部署：Vercel（前端）+ Render/Railway/Fly.io（后端）+ Neon/Supabase（数据库）
- 域名：Cloudflare 托管 DNS，前端绑定主域名，后端绑定 `api` 子域名

## 推荐域名结构

- `https://gipfel.example.com`：正式比赛网页
- `https://api.gipfel.example.com`：后端 API
- `https://streamlit.gipfel.example.com`：旧 Streamlit 兜底入口，稳定后可下线

## 当前已建设

- `web/`：Next.js 正式网页前端骨架
- `api/`：FastAPI 后端骨架
- `api/main.py`：健康检查、登录认证、账户组合、行情、K 线、下单、市场控制、管理员只读概览接口
- `api/schema.postgres.sql`：PostgreSQL 建表脚本
- `api/migrate_sqlite_to_postgres.py`：SQLite 到 PostgreSQL 数据迁移脚本
- `.gitignore`：屏蔽数据库、缓存、依赖、环境变量

## 当前安全开关

写入能力默认关闭，避免迁移阶段误改现网比赛数据：

```text
ENABLE_ORDER_WRITES=false
ENABLE_MARKET_WRITES=false
ENABLE_ADMIN_WRITES=false
```

保持 `false` 时，前端可以测试登录、查看资产、提交流程和管理面板，但不会真实写入关键比赛数据。完成数据库迁移和回归测试后，再逐项改为 `true`。

## Phase 1：只读正式网页

把公开行情、K 线图、比赛状态迁移到 Next.js。后端先只读当前 SQLite 或迁移后的 PostgreSQL。

验收标准：

- 正式网页能打开行情面板
- K 线图使用 TradingView Lightweight Charts
- 页面不依赖 Streamlit 组件
- 移动端和电脑端布局稳定

## Phase 2：认证和选手交易

迁移登录、角色、选手交易、持仓、记录。下单 API 必须通过认证后才允许写入。

当前进度：

- 已支持 `/auth/login`，兼容 Streamlit 旧密码格式 `salt:sha256(password+salt)`
- 已支持 `/auth/me`
- 已支持 `/portfolio`
- 已支持 `/orders`，只有 `ENABLE_ORDER_WRITES=true` 时真实写入
- 前端已接入登录表单、账户资产面板、交易委托表单

验收标准：

- 管理员和选手分角色登录
- 选手只能操作自己的账号
- 下单写入数据库并生成审计记录
- 与现有 Streamlit 交易结果一致

## Phase 3：管理员端

迁移开盘、收盘、用户管理、股票管理、审计日志。

当前进度：

- 已支持 `/admin/market/close`
- 已支持 `/admin/market/open`
- 已支持 `/admin/users`
- 已支持 `/admin/stocks`
- 已支持 `/admin/audit-logs`
- 管理员用户状态、密码重置、股票参数更新接口已预留，只有 `ENABLE_ADMIN_WRITES=true` 时真实写入

验收标准：

- 轮次不会因为部署或重启跳动
- 收盘结算可重复测试
- 管理操作都有审计日志
- 管理员界面在电脑端可稳定使用

## Phase 4：生产化

SQLite 切换为 PostgreSQL，配置备份、域名、HTTPS、监控。

当前进度：

- 已准备 PostgreSQL schema
- 已准备 SQLite 数据迁移脚本
- FastAPI 已能根据 `DATABASE_URL` 自动切换 PostgreSQL
- `/health` 会返回当前数据库后端、`TOKEN_SECRET` 是否配置、写入开关状态

验收标准：

- 数据库重启不丢数据
- 域名访问稳定
- 后端 API 有健康检查
- 每日自动备份

## 本地开发注意

不要在当前 Streamlit 生产仓库里混用本地服务器测试线上数据。正式开发时建议只运行构建和编译检查：

```bash
cd web
npm install
npm run build
```

```bash
cd api
pip install -r requirements.txt
python -m py_compile main.py
```

## 生产环境变量

前端：

```text
NEXT_PUBLIC_API_BASE=https://api.gipfel.example.com
```

后端：

```text
DATABASE_URL=postgresql://user:password@host:5432/gipfel
CORS_ALLOW_ORIGINS=https://gipfel.example.com
TOKEN_SECRET=replace-with-a-long-random-secret
ENABLE_ORDER_WRITES=false
ENABLE_MARKET_WRITES=false
ENABLE_ADMIN_WRITES=false
```

迁移 SQLite 数据：

```bash
cd api
pip install -r requirements.txt
set DATABASE_URL=postgresql://user:password@host:5432/gipfel
set SQLITE_DB_PATH=../data/stock_analysis.db
python migrate_sqlite_to_postgres.py
```

当前 `api/main.py` 同时支持 `DATABASE_URL` 和 `SQLITE_DB_PATH`，方便灰度过渡。
