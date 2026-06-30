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

export async function fetchMarket(): Promise<MarketSnapshot> {
  if (!API_BASE) return fallbackMarket;
  const res = await fetch(`${API_BASE}/market`, { cache: "no-store" });
  if (!res.ok) return fallbackMarket;
  return res.json();
}

export async function fetchCandles(symbol: string): Promise<Candle[]> {
  if (!API_BASE) return demoCandles(symbol);
  const res = await fetch(`${API_BASE}/stocks/${symbol}/kline`, { cache: "no-store" });
  if (!res.ok) return demoCandles(symbol);
  return res.json();
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
  if (!res.ok) throw new Error("login_failed");
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

export async function marketControl(token: string, action: "open" | "close") {
  if (!API_BASE || token === "demo-token") {
    return {
      accepted: false,
      reason: "demo_mode",
      detail: "演示模式未连接真实后端"
    };
  }
  const res = await fetch(`${API_BASE}/admin/market/${action}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!res.ok) throw new Error("market_control_failed");
  return res.json();
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

export function demoCandles(symbol: string): Candle[] {
  const seed = Array.from(symbol).reduce((sum, ch, i) => sum + ch.charCodeAt(0) * (i + 5), 0);
  const base = symbol === "YLIAO" ? 25 : symbol === "JGONG" ? 20 : symbol === "JXIAO" ? 15 : 10;
  let close = base;
  const start = Date.UTC(2026, 2, 20);
  return Array.from({ length: 72 }, (_, i) => {
    const date = new Date(start + i * 86400000).toISOString().slice(0, 10);
    const wave = Math.sin((i + seed) / 6) * base * 0.012;
    const drift = Math.sin((i + seed) / 17) * base * 0.004;
    const open = close;
    close = Math.max(0.5, open + wave + drift);
    const high = Math.max(open, close) + base * (0.008 + ((i + seed) % 7) * 0.002);
    const low = Math.min(open, close) - base * (0.008 + ((i + seed) % 5) * 0.002);
    return {
      time: date,
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(Math.max(0.01, low).toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: Math.round(6000 + Math.sin(i / 4) * 1200 + ((i + seed) % 9) * 320)
    };
  });
}
