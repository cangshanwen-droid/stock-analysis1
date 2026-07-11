# Gipfel 免费公网部署策略

目标：不买服务器，尽量让中国大陆用户能打开比赛平台。

当前可行路线：

1. 保留 Vercel 前端。
2. 给 Vercel 前端绑定自定义域名。
3. DNS 使用 Vercel 面向中国访问优化的 CNAME。
4. 后端继续使用 Render + Neon PostgreSQL。
5. 前端已内置行情短缓存和读接口兜底，减少 API 抖动带来的白屏。

## 推荐入口

优先使用：

```text
https://www.your-domain.ltd
```

暂时保留原入口：

```text
https://stock-analysis1-ten.vercel.app
```

## Vercel 域名解析

在域名 DNS 控制台添加：

```text
类型：CNAME
主机记录：www
记录值：cname-china.vercel-dns.com
```

然后在 Vercel 项目中添加：

```text
www.your-domain.ltd
```

根域名可以后续再做跳转到 `www`。

## 前端环境变量

```text
NEXT_PUBLIC_API_BASE=https://gipfel-trading-api.onrender.com
NEXT_PUBLIC_API_FALLBACKS=
```

如果后续增加备用 API，可填写：

```text
NEXT_PUBLIC_API_FALLBACKS=https://backup-api.example.com
```

多个备用地址用英文逗号分隔。

## 后端环境变量

比赛当前阶段建议：

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

等自定义域名确定后，可以把 CORS 收紧为：

```text
CORS_ALLOW_ORIGINS=https://stock-analysis1-ten.vercel.app,https://www.your-domain.ltd
```

## Zeabur 验证结果

已实际登录 Zeabur 检查：

- 新账号没有可用免费共享集群。
- 创建项目要求先购买服务器或绑定外部服务器。
- 因此 Zeabur 当前不适合作为“零成本公网部署”方案。

## EdgeOne 验证结果

已实际进入 Tencent Cloud / EdgeOne：

- EdgeOne Pages/Makers 更适合中国大陆访问。
- 但腾讯云国际站账号要求补全信息并绑定银行卡。
- 如果不想绑卡，暂不作为当前路线。

## 比赛前验收

域名生效后检查：

1. 自定义域名能打开行情面板。
2. 行情卡片显示真实股票价格。
3. K 线图能加载并显示 hover 信息。
4. `player1/player1` 能登录。
5. `admin/admin123` 能登录。
6. 管理员能开市、收市、回到第一轮。
7. 操作员能提交买入和卖出。

## 风险说明

这个方案主要改善前端入口访问。后端 API 仍然在海外免费/低成本平台，不能保证和国内服务器一样稳定，但已经是“不买服务器、不用局域网”条件下最现实的方案。
