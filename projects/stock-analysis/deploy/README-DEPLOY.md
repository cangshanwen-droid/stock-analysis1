# Gipfel Stock API — 云端部署说明

> 服务器：106.54.26.86 (腾讯云, Ubuntu 20.04, Python 3.8.10)
> SSH：`ssh -i ~/cangshanwen.pem ubuntu@106.54.26.86`
> 源码仓库：https://github.com/cangshanwen-droid/stock-analysis1.git（公开，禁止提交任何密钥）

## 服务拓扑

```
公网 80/443 (nginx)
  ├─ /api/*    → 127.0.0.1:8000  gipfel-api (FastAPI, 管理系统)
  └─ /*        → 127.0.0.1:8001  stock-api  (本服务, FastAPI + SQLite)
```

- 本服务目录：`/home/ubuntu/stock-api/`
- 入口文件：`app.py`（systemd 用 `uvicorn app:app`，**文件名必须是 app.py**）
- 数据库：`/home/ubuntu/stock-api/stocks.db`（SQLite）
- 前端静态资源：`/home/ubuntu/stock-api/static/`（web-build 产物，`GET /` 注入 AUTO_LOGIN_SCRIPT 免登录）

## systemd 服务

文件：`/etc/systemd/system/stock-api.service`

```ini
[Unit]
Description=Gipfel Stock Trading API
After=network.target
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/stock-api
ExecStart=/home/ubuntu/stock-api/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8001
Environment=ADMIN_KEY=GIPFEL_ADMIN_KEY（部署时生成，勿入库）
Restart=always
[Install]
WantedBy=multi-user.target
```

- **ADMIN_KEY**（生产密钥）：通过 `Environment=ADMIN_KEY=GIPFEL_ADMIN_KEY（部署时生成，勿入库）` 注入。
  `app.py` 中 `_require_admin` 读取 `os.environ.get("ADMIN_KEY", "gipfel-admin-dev")`，
  请求头 `X-Admin-Key` 必须与之一致，否则 403。
- 更新 service 后：`sudo systemctl daemon-reload && sudo systemctl restart stock-api`

## HTTPS（自签名证书，无域名方案）

- 证书：`/etc/ssl/gipfel.crt` + `/etc/ssl/gipfel.key`（`openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=106.54.26.86"`，有效期至 2027-08）
- nginx 配置：`/etc/nginx/sites-available/gipfel`（模板见仓库 `deploy/nginx-gipfel-ssl.conf`）
  - 443 server 已就绪（TLSv1.2/1.3，反代逻辑与 80 相同）
  - 80 当前直连服务（**腾讯云安全组未放行 TCP 443 入站**，见下）
- ⚠️ **待办（需人工）**：腾讯云控制台 → 安全组 → 入站规则放行 `TCP:443 来源 0.0.0.0/0`；
  放行后 `curl -k https://106.54.26.86/market` 应返回 200。
- 启用 80→443 强制重定向：把 nginx 配置 80 server 块中的 `return 301 https://$host$request_uri;` 取消注释（并注释其 location 块），`sudo nginx -t && sudo systemctl reload nginx`

## 更新部署（源码同步）

```bash
# 本地（仓库 projects/stock-analysis/api/stock_mini.py 与线上 app.py 保持同步）
scp -i ~/cangshanwen.pem api/stock_mini.py ubuntu@106.54.26.86:/home/ubuntu/stock-api/app.py
ssh -i ~/cangshanwen.pem ubuntu@106.54.26.86 "sudo systemctl restart stock-api"

# 线上验证
curl -s http://106.54.26.86/market | head -c 300
curl -s http://106.54.26.86/api/health
curl -s -H "X-Admin-Key: GIPFEL_ADMIN_KEY（部署时生成，勿入库）" http://106.54.26.86/admin/accounts
```

改线上文件前先备份：`cp app.py app.py.bak-$(date +%Y%m%d)`（既有备份：`app.py.bak-20260810`）。

## 关键端点

| 端点 | 说明 | 鉴权 |
|---|---|---|
| `GET /market` | 股票行情列表 | 无 |
| `POST /auth/login` | 登录（用户不存在自动建档，sha256 存密码） | 无 |
| `GET /admin/accounts` | 全账户监控总览 | `X-Admin-Key` |
| `GET /admin/accounts/{id}` | 单账户详情 | `X-Admin-Key` |
| `GET /admin/stocks` | 股票总览 | `X-Admin-Key` |
| `PATCH /admin/stocks/{symbol}` | 幸福度/碳排/人口联动调价 | `X-Admin-Key` |

## 运维速查

- 看日志：`sudo journalctl -u stock-api --no-pager -n 50`
- 服务状态：`sudo systemctl status stock-api`
- ⚠️ Ubuntu 20.04 = Python 3.8：类型注解必须用 `Dict/Tuple/List` 兼容写法，不能用 `dict[str, ...]` / `|`
- ⚠️ 桌面端 `fetchWithAdminKey` 目前硬编码旧 key `gipfel-admin`，云端改环境变量后会 403，需同步更新桌面端（从配置文件读 key，勿硬编码生产密钥进公开仓库）
