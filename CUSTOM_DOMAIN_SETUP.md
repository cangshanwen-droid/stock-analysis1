# Gipfel 自定义域名接入准备

目标：保留当前 Vercel 部署，同时给前端绑定一个更适合中国大陆访问的自定义域名。

## 推荐域名形式

优先使用 `www` 子域名：

```text
www.your-domain.ltd
```

根域名 `your-domain.ltd` 后续可以在 Vercel 中跳转到 `www`。

## Vercel 项目配置

项目：`stock-analysis1-ten`

前端环境变量：

```text
NEXT_PUBLIC_API_BASE=https://gipfel-trading-api.onrender.com
NEXT_PUBLIC_API_FALLBACKS=
```

如果后面增加备用 API，把备用 API 填到 `NEXT_PUBLIC_API_FALLBACKS`，多个地址用英文逗号分隔。

## DNS 解析

在域名服务商的 DNS 控制台添加：

```text
记录类型：CNAME
主机记录：www
记录值：cname-china.vercel-dns.com
TTL：默认
```

然后在 Vercel 项目 `Settings -> Domains` 添加：

```text
www.your-domain.ltd
```

Vercel 校验通过后会自动签发 HTTPS 证书。

## 后端 CORS

当前 API 支持通过 `CORS_ALLOW_ORIGINS` 控制允许访问的前端域名。

比赛前最省心配置：

```text
CORS_ALLOW_ORIGINS=*
```

如果要收紧安全范围，等域名确定后改成：

```text
CORS_ALLOW_ORIGINS=https://stock-analysis1-ten.vercel.app,https://www.your-domain.ltd
```

## 验收

域名生效后检查：

1. `https://www.your-domain.ltd` 能打开行情面板。
2. 行情卡片能显示真实价格。
3. K 线能加载。
4. `player1/player1` 能登录。
5. `admin/admin123` 能登录。
6. 管理员市场控制能执行。

## 注意

这个方案改善的是前端访问入口。后端 API 仍然在 Render，前端已经内置短缓存和读接口兜底逻辑，用来降低 API 抖动时对白屏和卡顿的影响。
