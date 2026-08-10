# Gipfel 股票模拟交易系统 — 开发者交接文档

> 给后续接手者的完整指南。仓库公开，**严禁**写入明文密码/密钥。

## 0. 仓库结构

```
projects/stock-analysis/          ← 本项目
├── api/                          ← FastAPI 后端
│   ├── main.py                   ← 全部端点 + 认证 + 缓存 + 中间件
│   ├── trading.py                ← 撮合引擎 (place_order / _match_buy / _match_sell)
│   ├── market_ops.py             ← 开盘/收盘/结算/价格公式
│   ├── db.py                     ← SQLite/PostgreSQL 双后端 + 连接重试
│   ├── schema.postgres.sql       ← 建表脚本
│   ├── Dockerfile                ← Render Docker 镜像
│   ├── start.sh                  ← 容器启动脚本
│   └── requirements.txt
├── web/                          ← Next.js 前端源码
│   ├── components/
│   │   ├── TradingWorkspace.tsx   ← 主组件 (~1650行)
│   │   ├── KlineChart.tsx        ← K线图 (lightweight-charts)
│   │   ├── ClientTradingWorkspace.tsx ← SSR 动态加载
│   │   ├── ClockDisplay.tsx      ← 实时时钟
│   │   └── KlineChart.tsx
│   ├── lib/
│   │   ├── api.ts                ← API 调用层 (缓存/重试/超时)
│   │   └── types.ts              ← TypeScript 类型
│   └── app/
│       ├── globals.css           ← 1413行纯手写 CSS
│       ├── layout.tsx
│       └── page.tsx
├── web-build/                    ← 前端静态构建产物 (由 FastAPI 直接服务)
└── render.yaml                   ← Render Blueprint (仓库根目录)
```

## 1. 架构

```
选手浏览器
   │  https://gipfel-trading-api.onrender.com
   ▼
Render 免费实例 (Docker, 1 worker)
   ├─ FastAPI 后端  api/main.py + api/trading.py + api/market_ops.py
   ├─ 静态前端      web-build/ (Next.js export, FastAPI 直接服务)
   └─ PostgreSQL    Neon 免费版 (DATABASE_URL 环境变量)
```

## 2. 部署流程

**推送 → 自动部署:**
```bash
git push origin master:main
```

Render 自动重新部署，约 2-5 分钟。前端改动需要按 §6 重建 web-build。

**环境变量 (Render 控制台):**
- `DATABASE_URL` — Neon PostgreSQL 连接串
- `TOKEN_SECRET` — HMAC token 签名密钥 (generateValue)
- `ADMIN_PASSWORD` — 管理员恢复密码 (未设置则恢复功能禁用)
- `ENABLE_ORDER_WRITES / MARKET_WRITES / ADMIN_WRITES` — 均应为 `true`

## 3. 核心业务规则

- 管理员和选手 **没有个人资金** (`users.balance` 恒为 0)
- 钱在**资金账户** (`fund_accounts`) 里，选手自己创建并注入初始资金
- 交易必须带 `account_id`，通过资金账户进行
- 轮次制：开盘 → 交易 → 收盘结算 → 生成 K 线 → 下一轮
- 价格公式：`target = prev × (buy_total/sell_total) × premium_factor × carbon_factor`
- 单轮价格涨跌幅限制在 ±10%

## 4. 安全设计

- `TOKEN_SECRET` 缺失 → **拒绝启动**
- `ADMIN_PASSWORD` 未设置 → 恢复功能禁用（无默认密码，无后门）
- 登录 token：HMAC-SHA256 签名，8 小时过期
- 密码哈希：`salt:sha256(password+salt)`
- 限流：PostgreSQL 跨进程滚动窗口 + 内存 fallback
- CORS：仅白名单域名，无 `*`
- `try_debit` 原子扣款：`UPDATE ... WHERE balance>=?`，防止并发超扣

## 5. 本地开发

```bash
cd projects/stock-analysis

# 种子数据库
python local_bootstrap.py

# 启动 (三开关全开)
TOKEN_SECRET=local-secret ADMIN_PASSWORD=local-admin \
ENABLE_ORDER_WRITES=true ENABLE_MARKET_WRITES=true ENABLE_ADMIN_WRITES=true \
uvicorn api.main:app --port 8001

# 冒烟测试 (53项)
python local_smoke.py

# 前端类型检查
npm --prefix web run typecheck
```

## 6. 修改前端后必须重建 web-build

```bash
cd projects/stock-analysis

# 1. 切 export 模式
sed -i 's/output: "standalone"/output: "export"/' web/next.config.mjs
npm --prefix web run build

# 2. 替换 web-build
rm -rf web-build/_next web-build/index.html
cp -r web/out/_next web-build/_next && cp web/out/index.html web-build/index.html

# 3. 恢复配置
sed -i 's/output: "export"/output: "standalone"/' web/next.config.mjs

# 4. 提交
git add web-build && git commit -m "build: rebuild web-build"
```

## 7. 运维操作

**健康检查:**
```bash
curl https://gipfel-trading-api.onrender.com/health
# 应返回 ok:true, database:true, 三个写开关 true
```

**模拟交易 (生成测试 K 线):**
```bash
# 1. admin 创建选手
TOKEN=$(curl -s -X POST .../auth/login -d '{"username":"admin",...}' | ...)
curl -X POST .../admin/users -H "Authorization: Bearer $TOKEN" -d '{"username":"u1","password":"p","role":"player"}'

# 2. 选手登录 + 创建资金账户
PTOKEN=$(curl -s -X POST .../auth/login -d '{"username":"u1",...}' | ...)
FA_ID=$(curl -X POST .../fund-accounts -H ... -d '{"name":"acc","initial_balance":50000000}' | ...)

# 3. 下单 → 闭市 → 开市 × N轮
for rnd in $(seq 1 15); do
  curl -X POST .../orders -H ... -d '{"username":"u1","symbol":"JGONG","side":"buy","price":10,"shares":100,"account_id":'$FA_ID'}'
  curl -X POST .../admin/market/close -H ... -d '{"confirmation":"confirm-close"}'
  curl -X POST .../admin/market/open -H ... -d '{"confirmation":"confirm-open"}'
done
```

⚠️ `POST /admin/market/reset-round1` 会清空交易记录和资金账户，慎用。

## 8. K线图视觉配置

| 参数 | 值 | 说明 |
|------|-----|------|
| 背景 | `#0F141E` | 深蓝黑 |
| 阳线 | `#F24957` | 红涨 (A股惯例) |
| 阴线 | `#2CB67D` | 绿跌 |
| MA5 | `#FFC107` 金 / `#F59E0B` 琥珀 (死叉时) | 5周期移动均线 |
| MA10 | `#0EA5E9` | 10周期移动均线 |
| 蜡烛间距 | 9px (min 6px) | 不挤 |
| 量柱透明度 | 55%/50% | 看得见 |
| 影线 | 真实 OHLC 数据 | 不造假 |

## 9. 与 Gipfel 管理系统的联通

桌面软件 (`projects/contract-manager/`) 通过 API 联通：

```
模拟计算 → 幸福度/碳排 → PATCH /admin/stocks/{symbol}
         → premium_rate, carbon_price 自动更新
         
仪表盘   ← 股价/涨跌幅   ← GET /market (30秒刷新)
Gipfel平台 ← 完整嵌入   ← WebView → https://gipfel-trading-api.onrender.com
```

区域-股票映射 (stock-sync.ts):
- A区 → JGONG
- B区 → JXIAO
- C区 → WULIU, YLIAO

## 10. 已知限制

- Neon 免费版空闲挂起 → 首个请求偶发超时 (keep-alive 每 5 分钟 ping 缓解)
- Render 免费实例 1-2 req/s 并发上限
- 单轮价格涨跌限制 ±10%，极端行情下不够灵活
- 无 token 撤销机制 (重置密码不吊销旧 token)
- web-build 与 web/ 漂移风险：每次改前端必须按 §6 重建

## 11. 多 AI 协同约定

1. **严禁 force push 到 main** — main 是唯一生产分支，Render 自动部署
2. **基于当前 main 工作** — 开始先 `git pull`，提交前再 `git pull`
3. **推送前检查** — `git fetch origin main && git log origin/main -1`
4. **改动 api/web/web-build/render.yaml 前先读本文档**
5. **不要在仓库写明文凭据** — 公开仓库
6. **后端改动跑 `local_smoke.py`** (53 项)；前端改动跑 `npm run typecheck`
7. **提交信息写清楚** "改了什么、为什么"

## 12. 凭据管理

| 凭据 | 位置 |
|------|------|
| Render 控制台 | dashboard.render.com |
| admin 登录密码 | 系统负责人保管 |
| ADMIN_PASSWORD | Render 控制台 Environment |
| DATABASE_URL | Render 控制台 Environment (Neon) |
| TOKEN_SECRET | Render 控制台 Environment (generateValue) |
