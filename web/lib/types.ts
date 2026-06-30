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
