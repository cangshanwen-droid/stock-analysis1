# Gipfel 正式网页迁移方案

当前 Streamlit 版本继续保留，用作现网兜底。正式网页迁移采用渐进式方式：

## 目标架构

- 前端：Next.js + TradingView Lightweight Charts
- 后端：FastAPI
- 数据库：PostgreSQL
- 部署：Vercel（前端）+ Render/Railway/Fly.io（后端）+ Neon/Supabase（数据库）
- 域名：Cloudflare 托管 DNS，前端绑定主域名，后端绑定 `api` 子域名

## 推荐域名结构

- `https://gipfel.example.com`：正式比赛网页
- `https://api.gipfel.example.com`：后端 API
- `https://streamlit.gipfel.example.com`：旧 Streamlit 兜底入口，可后续下线

## 当前已建立

- `web/`：Next.js 正式网页前端骨架
- `api/`：FastAPI 后端骨架
- `api/main.py`：健康检查、登录认证、账户组合、行情、K线只读接口
- `.gitignore`：屏蔽数据库、缓存、依赖、环境变量
- 下单接口已预留，但生产写入仍保持关闭，等待认证、数据库和结算测试完成后再启用
- 后端已支持 `DATABASE_URL`。配置后走 PostgreSQL；未配置时继续读取旧 SQLite
- `api/schema.postgres.sql`：PostgreSQL 建表脚本
- `api/migrate_sqlite_to_postgres.py`：SQLite 到 PostgreSQL 数据迁移脚本

## 迁移阶段

### Phase 1：只读正式网页

把公开行情、K线图、比赛状态迁到 Next.js。后端先只读当前 SQLite 或迁移后的 PostgreSQL。

验收标准：

- 正式网页能打开行情面板
- K线图使用 TradingView Lightweight Charts
- 页面不依赖 Streamlit 组件
- 移动端和电脑端布局稳定

### Phase 2：认证和选手端交易

迁移登录、角色、选手交易、持仓、记录。下单 API 需要通过认证后才允许写入。

当前进度：

- 已支持 `/auth/login`，兼容 Streamlit 旧密码格式 `salt:sha256(password+salt)`
- 已支持 `/auth/me`
- 已支持 `/portfolio`，返回资金、总资产、持仓、未成交委托、近期成交
- 前端已接入登录表单和账户资产面板
- `/orders` 已要求 Bearer token，但仍返回 `order_api_not_enabled_yet`
- `/orders` 已接入后端撮合服务；只有 `ENABLE_ORDER_WRITES=true` 时才真实写入数据库
- `/admin/market/close` 和 `/admin/market/open` 已接入后端市场控制服务；只有 `ENABLE_MARKET_WRITES=true` 时才真实写入数据库

验收标准：

- 管理员和选手分角色登录
- 选手只能操作自己的账号
- 下单写入数据库并生成审计记录
- 与现有 Streamlit 交易结果一致

写入开关：

```text
ENABLE_ORDER_WRITES=false
ENABLE_MARKET_WRITES=false
```

保持 `false` 时前端可以测试提交流程，但不会改比赛数据。只有完成迁移测试后才改为 `true`。

### Phase 3：管理员端

迁移开盘、收盘、撤销、重开、用户管理、股票管理。

验收标准：

- 轮次不会因部署或重启跳动
- 收盘结算可重复测试
- 管理操作都有审计日志

### Phase 4：生产化

SQLite 切换为 PostgreSQL，配置备份、域名、HTTPS、监控。

当前进度：

- 已准备 PostgreSQL schema
- 已准备 SQLite 数据迁移脚本
- FastAPI 已能根据 `DATABASE_URL` 自动切换 PostgreSQL
- `/health` 会返回当前数据库后端和 `TOKEN_SECRET` 是否已配置

验收标准：

- 数据库重启不丢数据
- 域名访问稳定
- 后端 API 有健康检查
- 每日自动备份

## 本地开发

不要在当前 Streamlit 生产仓库里混用本地服务器测试线上数据。正式开发时建议单独环境变量：

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
```

迁移旧 SQLite 数据：

```bash
cd api
pip install -r requirements.txt
set DATABASE_URL=postgresql://user:password@host:5432/gipfel
set SQLITE_DB_PATH=../data/stock_analysis.db
python migrate_sqlite_to_postgres.py
```

当前 `api/main.py` 同时支持 `DATABASE_URL` 和 `SQLITE_DB_PATH`，方便灰度过渡。
