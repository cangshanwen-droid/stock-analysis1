import type { AdminStock, AdminUser, AuditLog, Candle, HealthStatus, LoginResult, MarketSnapshot, PortfolioSnapshot } from "./types";

const PRIMARY_API_BASE = process.env.NEXT_PUBLIC_API_BASE || "https://gipfel-trading-api.onrender.com";
const API_FALLBACKS = (process.env.NEXT_PUBLIC_API_FALLBACKS || "")
  .split(",")
  .map((base) => base.trim())
  .filter(Boolean);
const API_BASES = Array.from(new Set([PRIMARY_API_BASE, ...API_FALLBACKS].filter(Boolean)))
  .map((base) => base.replace(/\/+$/, ""));
const MARKET_CACHE_KEY = "gipfel:last-market";
const CANDLE_CACHE_PREFIX = "gipfel:last-candles:";
const MARKET_CACHE_TTL = 2500;
const CANDLE_CACHE_TTL = 2500;
let pendingMarketRequest: Promise<MarketSnapshot> | null = null;
const pendingCandleRequests = new Map<string, Promise<Candle[]>>();

const fallbackMarket: MarketSnapshot = {
  round: 1,
  state: "open",
  stocks: [
    { symbol: "JGONG", name: "加工1公司", price: 20, change: 0, changePct: 0, manager: "", fundsLocked: false, companyBalance: 0 },
    { symbol: "JXIAO", name: "经销1公司", price: 15, change: 0, changePct: 0, manager: "", fundsLocked: false, companyBalance: 0 },
    { symbol: "WULIU", name: "物流1公司", price: 10, change: 0, changePct: 0, manager: "", fundsLocked: false, companyBalance: 0 },
    { symbol: "YLIAO", name: "原料1公司", price: 25, change: 0, changePct: 0, manager: "", fundsLocked: false, companyBalance: 0 }
  ]
};

function readCache<T>(key: string, maxAgeMs: number): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const cached = JSON.parse(raw) as { savedAt: number; data: T };
    if (!cached.savedAt || Date.now() - cached.savedAt > maxAgeMs) return null;
    return cached.data;
  } catch {
    return null;
  }
}

function writeCache<T>(key: string, data: T) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify({ savedAt: Date.now(), data }));
  } catch {
    // Browsers can reject storage in private mode. The live API path still works.
  }
}

function requestMethod(init?: RequestInit) {
  return String(init?.method || "GET").toUpperCase();
}

function isReadRequest(init?: RequestInit) {
  const method = requestMethod(init);
  return method === "GET" || method === "HEAD";
}

async function fetchWithRetry(input: string, init?: RequestInit, attempts = isReadRequest(init) ? 3 : 1): Promise<Response> {
  let lastError: unknown;
  for (let i = 0; i < attempts; i += 1) {
    try {
      const res = await fetch(input, init);
      if (res.ok || i === attempts - 1) return res;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 350 * (i + 1)));
  }
  throw lastError instanceof Error ? lastError : new Error("request_failed");
}

async function fetchApi(path: string, init?: RequestInit): Promise<Response> {
  if (!API_BASES.length) throw new Error("api_not_configured");
  const bases = isReadRequest(init) ? API_BASES : API_BASES.slice(0, 1);
  let lastError: unknown;
  for (const base of bases) {
    try {
      const res = await fetchWithRetry(`${base}${path}`, init);
      if (res.ok) return res;
      lastError = new Error(`api_${res.status}`);
      if (res.status === 401 || res.status === 403 || res.status === 400 || res.status === 409) return res;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError instanceof Error ? lastError : new Error("api_unavailable");
}

async function apiError(res: Response, fallback: string) {
  try {
    const data = await res.json();
    return new Error(data.detail || data.reason || data.message || fallback);
  } catch {
    return new Error(fallback);
  }
}

async function acceptedJsonOrThrow(res: Response, fallback: string) {
  if (!res.ok) throw await apiError(res, fallback);
  const data = await res.json();
  if (data.accepted === false) throw new Error(data.detail || data.reason || fallback);
  return data;
}

export async function fetchMarket(force = false): Promise<MarketSnapshot> {
  const cached = readCache<MarketSnapshot>(MARKET_CACHE_KEY, MARKET_CACHE_TTL);
  if (!force && cached?.stocks?.length) return cached;
  if (!force && pendingMarketRequest) return pendingMarketRequest;
  if (!API_BASES.length) return fallbackMarket;
  pendingMarketRequest = (async () => {
    try {
      const res = await fetchApi("/market");
      if (!res.ok) return fallbackMarket;
      const data = await res.json();
      if (Array.isArray(data.stocks) && data.stocks.length > 0) {
        writeCache(MARKET_CACHE_KEY, data);
        return data;
      }
      return fallbackMarket;
    } catch {
      return readCache<MarketSnapshot>(MARKET_CACHE_KEY, Number.MAX_SAFE_INTEGER) ?? fallbackMarket;
    } finally {
      pendingMarketRequest = null;
    }
  })();
  return pendingMarketRequest;
}

export async function fetchCandles(symbol: string, force = false): Promise<Candle[]> {
  const cacheKey = `${CANDLE_CACHE_PREFIX}${symbol}`;
  const cached = readCache<Candle[]>(cacheKey, CANDLE_CACHE_TTL);
  if (!force && cached?.length) return cached;
  const pending = pendingCandleRequests.get(symbol);
  if (!force && pending) return pending;
  if (!API_BASES.length) return demoCandles(symbol);
  const request = (async () => {
    try {
      const res = await fetchApi(`/stocks/${encodeURIComponent(symbol)}/kline`);
      if (!res.ok) return demoCandles(symbol);
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) {
        writeCache(cacheKey, data);
        return data;
      }
      return demoCandles(symbol);
    } catch {
      return readCache<Candle[]>(cacheKey, Number.MAX_SAFE_INTEGER) ?? demoCandles(symbol);
    } finally {
      pendingCandleRequests.delete(symbol);
    }
  })();
  pendingCandleRequests.set(symbol, request);
  return request;
}

export function prefetchCandles(symbols: string[]) {
  for (const symbol of symbols) {
    void fetchCandles(symbol);
  }
}

export function clearPublicReadCache(symbol?: string) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(MARKET_CACHE_KEY);
    if (symbol) {
      window.localStorage.removeItem(`${CANDLE_CACHE_PREFIX}${symbol}`);
    }
  } catch {
    // Cache clearing is a speed optimization; failed storage access should not block trading.
  }
  pendingMarketRequest = null;
  if (symbol) pendingCandleRequests.delete(symbol);
}

export async function fetchHealth(): Promise<HealthStatus> {
  if (!API_BASES.length) {
    return {
      ok: false,
      database: false,
      backend: "demo",
      tokenSecretConfigured: false,
      orderWritesEnabled: false,
      marketWritesEnabled: false,
      adminWritesEnabled: false
    };
  }
  const res = await fetchApi("/health");
  if (!res.ok) throw new Error("health_failed");
  return res.json();
}

export async function login(username: string, password: string): Promise<LoginResult> {
  if (!API_BASES.length) {
    return {
      accessToken: "demo-token",
      tokenType: "bearer",
      expiresIn: 28800,
      user: { username, role: username === "admin" ? "admin" : "player", balance: 1000000 }
    };
  }
  const res = await fetchApi("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  if (!res.ok) throw await apiError(res, "login_failed");
  return res.json();
}

export async function fetchMyCompanies(token: string) {
  if (!API_BASES.length || token === "demo-token") return [];
  const res = await fetchApi("/my-companies", {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!res.ok) return [];
  return res.json();
}

export async function fetchPortfolio(token: string): Promise<PortfolioSnapshot> {
  if (!API_BASES.length || token === "demo-token") {
    return {
      user: { username: "player1", role: "player", balance: 1000000 },
      summary: { marketValue: 0, totalAssets: 1000000, totalPnl: 0, pnlRatio: 0 },
      positions: [],
      orders: [],
      recentTrades: []
    };
  }
  const res = await fetchApi("/portfolio", {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!res.ok) throw new Error("portfolio_failed");
  return res.json();
}

export async function submitOrder(token: string, order: {
  username: string;
  symbol: string;
  side: "buy" | "sell";
  price: number;
  shares: number;
  company_symbol?: string;
}) {
  if (!API_BASES.length || token === "demo-token") {
    return {
      accepted: false,
      reason: "demo_mode",
      detail: "演示模式未连接真实后端"
    };
  }
  const res = await fetchApi("/orders", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify(order)
  });
  if (!res.ok) throw new Error("order_failed");
  return res.json();
}

export async function marketControl(token: string, action: "open" | "close" | "reset" | "previous", _confirmation: string) {
  if (!API_BASES.length || token === "demo-token") {
    return {
      accepted: false,
      reason: "demo_mode",
      detail: "演示模式未连接真实后端"
    };
  }
  const path = action === "reset" ? "reset-round1" : action === "previous" ? "previous-round" : action;
  const confirmationCode = action === "close"
    ? "confirm-close"
    : action === "open"
      ? "confirm-open"
      : action === "previous"
        ? "confirm-previous-round"
        : "confirm-reset-round1";
  const res = await fetchApi(`/admin/market/${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ confirmation: confirmationCode })
  });
  if (!res.ok) throw await apiError(res, "market_control_failed");
  const data = await res.json();
  if (data.accepted === false) throw new Error(data.detail || data.reason || "market_control_failed");
  return data;
}

export async function fetchAdminOverview(token: string): Promise<{
  users: AdminUser[];
  stocks: AdminStock[];
  auditLogs: AuditLog[];
}> {
  if (!API_BASES.length || token === "demo-token") {
    return { users: [], stocks: [], auditLogs: [] };
  }
  const headers = { Authorization: `Bearer ${token}` };
  const [usersRes, stocksRes, logsRes] = await Promise.all([
    fetchApi("/admin/users", { headers, cache: "no-store" }),
    fetchApi("/admin/stocks", { headers, cache: "no-store" }),
    fetchApi("/admin/audit-logs?limit=12", { headers, cache: "no-store" })
  ]);
  if (!usersRes.ok || !stocksRes.ok || !logsRes.ok) throw new Error("admin_overview_failed");
  return {
    users: await usersRes.json(),
    stocks: await stocksRes.json(),
    auditLogs: await logsRes.json()
  };
}

export async function createAdminUser(token: string, payload: {
  username: string;
  password: string;
  role: "admin" | "player";
}) {
  const res = await fetchApi("/admin/users", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify(payload)
  });
  return acceptedJsonOrThrow(res, "create_user_failed");
}

export async function updateAdminUserStatus(token: string, username: string, status: "active" | "disabled") {
  const res = await fetchApi(`/admin/users/${encodeURIComponent(username)}/status`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ status })
  });
  return acceptedJsonOrThrow(res, "update_user_status_failed");
}

export async function resetAdminUserPassword(token: string, username: string, password: string) {
  const res = await fetchApi(`/admin/users/${encodeURIComponent(username)}/password`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ password })
  });
  return acceptedJsonOrThrow(res, "reset_user_password_failed");
}

export async function deleteAdminUser(token: string, username: string) {
  const res = await fetchApi(`/admin/users/${encodeURIComponent(username)}`, {
    method: "DELETE",
    headers: {
      Authorization: `Bearer ${token}`
    }
  });
  if (!res.ok) throw await apiError(res, "delete_user_failed");
  const data = await res.json();
  if (data.accepted === false) throw new Error(data.detail || data.reason || "delete_user_failed");
  return data;
}

export async function createAdminStock(token: string, payload: {
  symbol: string;
  name: string;
  revenue: number;
  total_shares: number;
  industry_pe: number;
  carbon_price: number;
  industry_carbon_mean: number;
  premium_rate: number;
}) {
  const res = await fetchApi("/admin/stocks", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify(payload)
  });
  return acceptedJsonOrThrow(res, "create_stock_failed");
}

export async function updateAdminStock(token: string, symbol: string, payload: {
  revenue?: number;
  total_shares?: number;
  industry_pe?: number;
  carbon_price?: number;
  industry_carbon_mean?: number;
  premium_rate?: number;
}) {
  const res = await fetchApi(`/admin/stocks/${encodeURIComponent(symbol)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify(payload)
  });
  return acceptedJsonOrThrow(res, "update_stock_failed");
}

export async function deleteAdminStock(token: string, symbol: string) {
  const res = await fetchApi(`/admin/stocks/${encodeURIComponent(symbol)}`, {
    method: "DELETE",
    headers: {
      Authorization: `Bearer ${token}`
    }
  });
  if (!res.ok) throw await apiError(res, "delete_stock_failed");
  const data = await res.json();
  if (data.accepted === false) throw new Error(data.detail || data.reason || "delete_stock_failed");
  return data;
}

export async function runDbMigration(token: string) {
  const res = await fetchApi("/admin/db/migrate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({})
  });
  if (!res.ok) throw await apiError(res, "migration_failed");
  return res.json();
}

export async function confirmMyCompanyFunds(token: string, initFunds: number) {
  const res = await fetchApi("/my-company/confirm-funds", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ init_funds: initFunds })
  });
  if (!res.ok) throw await apiError(res, "confirm_funds_failed");
  return res.json();
}

export async function setStockManager(token: string, symbol: string, manager: string) {
  const res = await fetchApi(`/admin/stocks/${encodeURIComponent(symbol)}/manager`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ manager })
  });
  if (!res.ok) throw await apiError(res, "set_manager_failed");
  return res.json();
}

export async function confirmStockFunds(token: string, symbol: string, initFunds: number) {
  const res = await fetchApi(`/admin/stocks/${encodeURIComponent(symbol)}/confirm-funds`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ init_funds: initFunds })
  });
  if (!res.ok) throw await apiError(res, "confirm_funds_failed");
  return res.json();
}

export function demoCandles(symbol: string): Candle[] {
  const seed = Array.from(symbol).reduce((sum, ch, i) => sum + ch.charCodeAt(0) * (i + 5), 0);
  const base = symbol === "YLIAO" ? 25 : symbol === "JGONG" ? 20 : symbol === "JXIAO" ? 15 : 10;
  let close = base;
  const start = Date.UTC(2000, 0, 1);
  return Array.from({ length: 72 }, (_, i) => {
    const date = new Date(start + i * 86400000).toISOString().slice(0, 10);
    const wave = Math.sin((i + seed) / 6) * base * 0.012;
    const drift = Math.sin((i + seed) / 17) * base * 0.004;
    const open = close;
    close = Math.max(0.5, open + wave + drift);
    const high = Math.max(open, close) + base * (0.008 + ((i + seed) % 7) * 0.002);
    const low = Math.min(open, close) - base * (0.008 + ((i + seed) % 5) * 0.002);
    return {
      round: i + 1,
      time: date,
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(Math.max(0.01, low).toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: Math.round(6000 + Math.sin(i / 4) * 1200 + ((i + seed) % 9) * 320)
    };
  });
}
