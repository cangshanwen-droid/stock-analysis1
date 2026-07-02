# Gipfel 免费公网多镜像部署

这个方案用于“不用 Streamlit、不用局域网、尽量免费、尽量让中国大陆能打开”的场景。它不能像付费香港服务器一样保证稳定，但会比单独依赖 Vercel/Render 更抗抖。

## 推荐结构

- 前端主站：Vercel
- 前端备用：Zeabur
- API 主站：Render
- API 备用：Zeabur API 或其他免费容器平台
- 数据库：Neon PostgreSQL，保持持久化

## 前端环境变量

```text
NEXT_PUBLIC_API_BASE=https://gipfel-trading-api.onrender.com
NEXT_PUBLIC_API_FALLBACKS=https://your-zeabur-api.zeabur.app,https://api.gipfel.example.com
```

`NEXT_PUBLIC_API_BASE` 是主 API。`NEXT_PUBLIC_API_FALLBACKS` 可以填多个备用 API，用英文逗号分隔。前端会对行情、K 线、资产和管理概览这类读接口自动按顺序尝试。

下单、开市、收市、回到第一轮、创建/删除账号、增删股票等写操作只走主 API，避免网络超时后重复提交。

## 后端环境变量

```text
DATABASE_URL=postgresql://...
CORS_ALLOW_ORIGINS=*
TOKEN_SECRET=replace-with-a-long-random-secret
TOKEN_TTL_SECONDS=28800
ENABLE_ORDER_WRITES=true
ENABLE_MARKET_WRITES=true
ENABLE_ADMIN_WRITES=true
ADMIN_PASSWORD=admin123
```

比赛前确认三个写入开关都是 `true`，否则管理员开市、收市、添加股票和操作员交易都会被拒绝。

## Zeabur 部署要点

1. 连接 GitHub 仓库。
2. 使用根目录的 `zeabur.json`，它会识别两个项目：
   - `web`：Next.js 前端
   - `api`：Docker 后端
3. 给 `api` 项目配置数据库和后端环境变量。
4. 给 `web` 项目配置 `NEXT_PUBLIC_API_BASE` 和 `NEXT_PUBLIC_API_FALLBACKS`。
5. GitHub Actions 已监听 `main` 分支，可手动触发 Zeabur 部署。

## 比赛前检查

- 打开前端首页，行情面板能显示真实股票。
- `https://你的API域名/health` 返回 `ok: true`。
- 管理员 `admin/admin123` 可以登录。
- 操作员 `player1/player1` 可以登录。
- 管理员能开市、收市、回到第一轮。
- 操作员能提交买入/卖出。
- K 线 hover 能显示开高低收和成交量。

## 免费方案的底线

免费公网没有绝对稳定。比赛当天建议准备至少两个入口：

- Vercel 入口
- Zeabur 入口

如果一个地区打不开，就切另一个入口。

如果要让多个 API 同时可用于登录和交易，必须确保它们连接同一个 PostgreSQL，并且 `TOKEN_SECRET` 完全相同。
