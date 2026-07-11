# Gipfel Custom Domain Setup

Domain: `gipfel.ltd`

Primary site URL:

```text
https://www.gipfel.ltd
```

Vercel project:

```text
gipfel-stock-bsc/stock-analysis1
```

Current Vercel aliases:

- `stock-analysis1-ten.vercel.app`
- `www.gipfel.ltd`
- `gipfel.ltd` redirects to `www.gipfel.ltd`

## DNS Records

Add these records in the DNS control panel where `gipfel.ltd` is managed.

### Root Domain

```text
Type: A
Name: @
Value: 216.198.79.1
```

### WWW Domain

Current DNS record:

```text
Type: CNAME
Name: www
Value: 9be3d8809316109e.vercel-dns-017.com.
```

Previous test value:

```text
Type: CNAME
Name: www
Value: cname-china.vercel-dns.com
```

`cname-china.vercel-dns.com` was tested first. Because mainland access was still unstable, `www` was switched to the Vercel-recommended project CNAME on 2026-07-03.

## Frontend Environment

```text
NEXT_PUBLIC_API_BASE=https://gipfel-trading-api.onrender.com
NEXT_PUBLIC_API_FALLBACKS=
```

## Backend CORS

Before the custom domain is verified, keep:

```text
CORS_ALLOW_ORIGINS=*
```

After the domain is stable, this can be tightened to:

```text
CORS_ALLOW_ORIGINS=https://stock-analysis1-ten.vercel.app,https://www.gipfel.ltd
```

## Checks After DNS Propagation

1. Open `https://www.gipfel.ltd`.
2. Confirm the market dashboard loads.
3. Confirm K-line data loads.
4. Login with `player1/player1`.
5. Login with `admin/admin123`.
6. Test admin market controls.
