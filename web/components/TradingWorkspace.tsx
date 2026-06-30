"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, BarChart3, ClipboardList, Shield, Wallet } from "lucide-react";
import { fetchCandles, fetchMarket } from "../lib/api";
import type { Candle, MarketSnapshot, StockQuote } from "../lib/types";
import { KlineChart } from "./KlineChart";

function fmtMoney(value: number) {
  return `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
}

function cls(value: number) {
  return value >= 0 ? "up" : "down";
}

export function TradingWorkspace() {
  const [market, setMarket] = useState<MarketSnapshot | null>(null);
  const [selected, setSelected] = useState("JGONG");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [side, setSide] = useState<"buy" | "sell">("buy");

  useEffect(() => {
    let alive = true;
    fetchMarket().then((data) => {
      if (!alive) return;
      setMarket(data);
      if (data.stocks[0]) setSelected(data.stocks[0].symbol);
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    fetchCandles(selected).then((data) => {
      if (alive) setCandles(data);
    });
    return () => {
      alive = false;
    };
  }, [selected]);

  const stocks = market?.stocks ?? [];
  const current: StockQuote | undefined = useMemo(
    () => stocks.find((s) => s.symbol === selected) ?? stocks[0],
    [selected, stocks]
  );

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <strong>Gipfel</strong>
          <span>双镜智能投资竞赛平台</span>
        </div>
        <div className="nav">
          <button className="active"><BarChart3 size={17} /> 行情面板</button>
          <button><Activity size={17} /> 交易大厅</button>
          <button><Wallet size={17} /> 持仓资产</button>
          <button><ClipboardList size={17} /> 委托记录</button>
          <button><Shield size={17} /> 管理控制</button>
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <div>
            <div className="meta">Trading Arena</div>
            <strong>股票交易竞赛平台</strong>
          </div>
          <button className="ghost">登录交易</button>
        </div>

        <section className="status-strip">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span className="status-dot" />
            <div>
              <strong>第 {market?.round ?? 1} 轮 · {market?.state === "closed" ? "已闭市" : "交易中"}</strong>
              <div className="meta">实时撮合 · 收盘结算 · 专业行情展示</div>
            </div>
          </div>
          <div className="meta">PostgreSQL API Ready</div>
        </section>

        <section className="quote-grid">
          {stocks.map((stock) => (
            <button className="card" key={stock.symbol} onClick={() => setSelected(stock.symbol)}>
              <div className="symbol">{stock.symbol}</div>
              <div className="name">{stock.name}</div>
              <div className={`price ${cls(stock.change)}`}>{fmtMoney(stock.price)}</div>
              <div className={cls(stock.change)}>
                {stock.change >= 0 ? "+" : ""}{stock.change.toFixed(2)} ({stock.changePct.toFixed(2)}%)
              </div>
            </button>
          ))}
        </section>

        <section className="workspace">
          <div className="chart-card">
            <div className="chart-head">
              <div>
                <strong>{current?.name ?? "公司"} · {current?.symbol ?? "-"}</strong>
                <div className="meta">红涨绿跌 · 十字光标 · 成交量</div>
              </div>
              <div className={cls(current?.change ?? 0)}>
                {current ? fmtMoney(current.price) : "--"}
              </div>
            </div>
            <KlineChart candles={candles} />
          </div>

          <aside className="ticket">
            <h2>交易委托</h2>
            <div className="segmented">
              <button className={side === "buy" ? "buy" : ""} onClick={() => setSide("buy")}>买入</button>
              <button onClick={() => setSide("sell")}>卖出</button>
            </div>
            <div className="form-grid">
              <div className="field">
                <label>公司股票</label>
                <select value={selected} onChange={(e) => setSelected(e.target.value)}>
                  {stocks.map((stock) => (
                    <option key={stock.symbol} value={stock.symbol}>{stock.name}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>委托价格</label>
                <input defaultValue={current?.price.toFixed(2) ?? "0.00"} />
              </div>
              <div className="field">
                <label>委托数量</label>
                <input defaultValue="100" />
              </div>
              <button className="primary">{side === "buy" ? "提交买入" : "提交卖出"}</button>
            </div>

            <div className="mini-table">
              <div className="row"><span>可用资金</span><strong>¥1,000,000</strong></div>
              <div className="row"><span>当前价格</span><strong>{current ? fmtMoney(current.price) : "--"}</strong></div>
              <div className="row"><span>委托模式</span><strong>限价撮合</strong></div>
              <div className="row"><span>数据源</span><strong>API / Demo fallback</strong></div>
            </div>
          </aside>
        </section>
      </main>
    </div>
  );
}
