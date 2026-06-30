export type StockQuote = {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePct: number;
};

export type Candle = {
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
