"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, BarChart3, ClipboardList, Shield, Wallet } from "lucide-react";
import { fetchCandles, fetchMarket, fetchPortfolio, login } from "../lib/api";
import type { Candle, MarketSnapshot, PortfolioSnapshot, StockQuote, UserSession } from "../lib/types";
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
  const [token, setToken] = useState<string>("");
  const [user, setUser] = useState<UserSession | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot | null>(null);
  const [loginName, setLoginName] = useState("player1");
  const [loginPassword, setLoginPassword] = useState("player1");
  const [loginError, setLoginError] = useState("");

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

  useEffect(() => {
    if (!token) return;
    let alive = true;
    fetchPortfolio(token)
      .then((data) => {
        if (!alive) return;
        setPortfolio(data);
        setUser(data.user);
      })
      .catch(() => {
        if (alive) setPortfolio(null);
      });
    return () => {
      alive = false;
    };
  }, [token]);

  async function submitLogin() {
    setLoginError("");
    try {
      const data = await login(loginName.trim(), loginPassword);
      setToken(data.accessToken);
      setUser(data.user);
      setPortfolio(null);
    } catch {
      setLoginError("账号或密码不正确");
    }
  }

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
          {user ? (
            <button className="ghost" onClick={() => { setToken(""); setUser(null); setPortfolio(null); }}>
              {user.username} · 退出
            </button>
          ) : (
            <button className="ghost" onClick={submitLogin}>登录交易</button>
          )}
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
            {!user && (
              <div className="login-box">
                <div className="field">
                  <label>账号</label>
                  <input value={loginName} onChange={(e) => setLoginName(e.target.value)} />
                </div>
                <div className="field">
                  <label>密码</label>
                  <input type="password" value={loginPassword} onChange={(e) => setLoginPassword(e.target.value)} />
                </div>
                <button className="primary" onClick={submitLogin}>登录交易</button>
                {loginError && <div className="error-text">{loginError}</div>}
              </div>
            )}
            {user && (
              <div className="account-box">
                <div className="row"><span>操作员</span><strong>{user.username}</strong></div>
                <div className="row"><span>角色</span><strong>{user.role === "admin" ? "管理员" : "选手"}</strong></div>
                <div className="row"><span>可用资金</span><strong>{fmtMoney(portfolio?.user.balance ?? user.balance)}</strong></div>
                <div className="row"><span>总资产</span><strong>{fmtMoney(portfolio?.summary.totalAssets ?? user.balance)}</strong></div>
                <div className="row"><span>浮动盈亏</span><strong className={cls(portfolio?.summary.totalPnl ?? 0)}>{fmtMoney(portfolio?.summary.totalPnl ?? 0)}</strong></div>
              </div>
            )}
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
              <button className="primary" disabled={!user}>{side === "buy" ? "提交买入" : "提交卖出"}</button>
            </div>

            <div className="mini-table">
              <div className="row"><span>当前价格</span><strong>{current ? fmtMoney(current.price) : "--"}</strong></div>
              <div className="row"><span>委托模式</span><strong>限价撮合</strong></div>
              <div className="row"><span>数据源</span><strong>API / Demo fallback</strong></div>
            </div>
            {portfolio?.positions.length ? (
              <div className="positions">
                <div className="section-caption">当前持仓</div>
                {portfolio.positions.map((pos) => (
                  <div className="position-row" key={pos.symbol}>
                    <div>
                      <strong>{pos.name}</strong>
                      <span>{pos.shares} 股 · 成本 {fmtMoney(pos.avgCost)}</span>
                    </div>
                    <div className={cls(pos.pnl)}>{fmtMoney(pos.pnl)}</div>
                  </div>
                ))}
              </div>
            ) : null}
          </aside>
        </section>
      </main>
    </div>
  );
}
