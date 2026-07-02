import type { AdminStock, AdminUser, AuditLog, Candle, LoginResult, MarketSnapshot, PortfolioSnapshot } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";

const fallbackMarket: MarketSnapshot = {
  round: 1,
  state: "open",
  stocks: [
    { symbol: "JGONG", name: "加工1公司", price: 20, change: 0, changePct: 0 },
    { symbol: "JXIAO", name: "经销1公司", price: 15, change: 0, changePct: 0 },
    { symbol: "WULIU", name: "物流1公司", price: 10, change: 0, changePct: 0 },
    { symbol: "YLIAO", name: "原料1公司", price: 25, change: 0, changePct: 0 }
  ]
};

async function fetchWithRetry(input: string, init?: RequestInit, attempts = 3): Promise<Response> {
  let lastError: unknown;
  for (let i = 0; i < attempts; i += 1) {
    try {
      const res = await fetch(input, init);
      if (res.ok || i === attempts - 1) return res;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 800 * (i + 1)));
  }
  throw lastError instanceof Error ? lastError : new Error("request_failed");
}

async function apiError(res: Response, fallback: string) {
  try {
    const data = await res.json();
    return new Error(data.detail || data.reason || data.message || fallback);
  } catch {
    return new Error(fallback);
  }
}

export async function fetchMarket(): Promise<MarketSnapshot> {
  if (!API_BASE) return fallbackMarket;
  try {
    const res = await fetchWithRetry(`${API_BASE}/market`, { cache: "no-store" });
    if (!res.ok) return fallbackMarket;
    const data = await res.json();
    return Array.isArray(data.stocks) && data.stocks.length > 0 ? data : fallbackMarket;
  } catch {
    return fallbackMarket;
  }
}

export async function fetchCandles(symbol: string): Promise<Candle[]> {
  if (!API_BASE) return demoCandles(symbol);
  try {
    const res = await fetchWithRetry(`${API_BASE}/stocks/${symbol}/kline`, { cache: "no-store" });
    if (!res.ok) return demoCandles(symbol);
    const data = await res.json();
    return Array.isArray(data) && data.length > 0 ? data : demoCandles(symbol);
  } catch {
    return demoCandles(symbol);
  }
}

export async function login(username: string, password: string): Promise<LoginResult> {
  if (!API_BASE) {
    return {
      accessToken: "demo-token",
      tokenType: "bearer",
      expiresIn: 28800,
      user: { username, role: username === "admin" ? "admin" : "player", balance: 1000000 }
    };
  }
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  if (!res.ok) throw await apiError(res, "login_failed");
  return res.json();
}

export async function fetchPortfolio(token: string): Promise<PortfolioSnapshot> {
  if (!API_BASE || token === "demo-token") {
    return {
      user: { username: "player1", role: "player", balance: 1000000 },
      summary: { marketValue: 0, totalAssets: 1000000, totalPnl: 0, pnlRatio: 0 },
      positions: [],
      orders: [],
      recentTrades: []
    };
  }
  const res = await fetch(`${API_BASE}/portfolio`, {
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
}) {
  if (!API_BASE || token === "demo-token") {
    return {
      accepted: false,
      reason: "demo_mode",
      detail: "演示模式未连接真实后端"
    };
  }
  const res = await fetch(`${API_BASE}/orders`, {
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

export async function marketControl(token: string, action: "open" | "close" | "reset", _confirmation: string) {
  if (!API_BASE || token === "demo-token") {
    return {
      accepted: false,
      reason: "demo_mode",
      detail: "演示模式未连接真实后端"
    };
  }
  const path = action === "reset" ? "reset-round1" : action;
  const confirmationCode = action === "close"
    ? "confirm-close"
    : action === "open"
      ? "confirm-open"
      : "confirm-reset-round1";
  const res = await fetch(`${API_BASE}/admin/market/${path}`, {
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
  if (!API_BASE || token === "demo-token") {
    return { users: [], stocks: [], auditLogs: [] };
  }
  const headers = { Authorization: `Bearer ${token}` };
  const [usersRes, stocksRes, logsRes] = await Promise.all([
    fetch(`${API_BASE}/admin/users`, { headers, cache: "no-store" }),
    fetch(`${API_BASE}/admin/stocks`, { headers, cache: "no-store" }),
    fetch(`${API_BASE}/admin/audit-logs?limit=12`, { headers, cache: "no-store" })
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
  const res = await fetch(`${API_BASE}/admin/users`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify(payload)
  });
  if (!res.ok) throw new Error("create_user_failed");
  return res.json();
}

export async function updateAdminUserStatus(token: string, username: string, status: "active" | "disabled") {
  const res = await fetch(`${API_BASE}/admin/users/${encodeURIComponent(username)}/status`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ status })
  });
  if (!res.ok) throw new Error("update_user_status_failed");
  return res.json();
}

export async function resetAdminUserPassword(token: string, username: string, password: string) {
  const res = await fetch(`${API_BASE}/admin/users/${encodeURIComponent(username)}/password`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ password })
  });
  if (!res.ok) throw new Error("reset_user_password_failed");
  return res.json();
}

export async function deleteAdminUser(token: string, username: string) {
  const res = await fetch(`${API_BASE}/admin/users/${encodeURIComponent(username)}`, {
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
  const res = await fetch(`${API_BASE}/admin/stocks`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify(payload)
  });
  if (!res.ok) throw new Error("create_stock_failed");
  return res.json();
}

export async function updateAdminStock(token: string, symbol: string, payload: {
  revenue?: number;
  total_shares?: number;
  industry_pe?: number;
  carbon_price?: number;
  industry_carbon_mean?: number;
  premium_rate?: number;
}) {
  const res = await fetch(`${API_BASE}/admin/stocks/${encodeURIComponent(symbol)}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify(payload)
  });
  if (!res.ok) throw new Error("update_stock_failed");
  return res.json();
}

export async function deleteAdminStock(token: string, symbol: string) {
  const res = await fetch(`${API_BASE}/admin/stocks/${encodeURIComponent(symbol)}`, {
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
