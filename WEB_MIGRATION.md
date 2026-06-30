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
- `api/main.py`：健康检查、行情、K线只读接口
- `.gitignore`：屏蔽数据库、缓存、依赖、环境变量

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

验收标准：

- 管理员和选手分角色登录
- 选手只能操作自己的账号
- 下单写入数据库并生成审计记录
- 与现有 Streamlit 交易结果一致

### Phase 3：管理员端

迁移开盘、收盘、撤销、重开、用户管理、股票管理。

验收标准：

- 轮次不会因部署或重启跳动
- 收盘结算可重复测试
- 管理操作都有审计日志

### Phase 4：生产化

SQLite 切换为 PostgreSQL，配置备份、域名、HTTPS、监控。

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
DATABASE_URL=postgresql://...
CORS_ALLOW_ORIGINS=https://gipfel.example.com
```

当前 `api/main.py` 仍支持 `SQLITE_DB_PATH` 读取旧 SQLite，方便过渡。
