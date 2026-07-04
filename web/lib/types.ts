export type StockQuote = {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePct: number;
  manager: string;
  fundsLocked: boolean;
  companyBalance: number;
};

export type Candle = {
  round: number;
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type MarketSnapshot = {
  round: number;
  state: "open" | "closed";
  stocks: StockQuote[];
};

export type HealthStatus = {
  ok: boolean;
  database: boolean;
  backend: string;
  tokenSecretConfigured: boolean;
  orderWritesEnabled: boolean;
  marketWritesEnabled: boolean;
  adminWritesEnabled: boolean;
};

export type UserSession = {
  username: string;
  role: "admin" | "player";
  balance: number;
};

export type LoginResult = {
  accessToken: string;
  tokenType: "bearer";
  expiresIn: number;
  user: UserSession;
};

export type Position = {
  symbol: string;
  name: string;
  shares: number;
  avgCost: number;
  currentPrice: number;
  marketValue: number;
  pnl: number;
  pnlRatio: number;
};

export type PortfolioSnapshot = {
  user: UserSession;
  summary: {
    marketValue: number;
    totalAssets: number;
    totalPnl: number;
    pnlRatio: number;
  };
  positions: Position[];
  orders: Array<Record<string, unknown>>;
  recentTrades: Array<Record<string, unknown>>;
};

export type AdminUser = {
  id: number;
  username: string;
  role: "admin" | "player";
  status: string;
  balance: number;
  createdAt: string;
};

export type AdminStock = {
  id: number;
  symbol: string;
  name: string;
  price: number;
  previousClose: number;
  isDeleted: boolean;
  totalShares: number;
  revenue: number;
  industryPe: number;
  carbonPrice: number;
  industryCarbonMean: number;
  premiumRate: number;
  initFunds: number;
  balance: number;
  manager: string;
  fundsLocked: boolean;
  lastUpdate: string;
};

export type AuditLog = {
  actor: string;
  action: string;
  target: string;
  detail: string;
  createdAt: string;
};
