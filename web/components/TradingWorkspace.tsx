"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, BarChart3, ClipboardList, Shield, Wallet } from "lucide-react";
import {
  createAdminUser,
  fetchAdminOverview,
  fetchCandles,
  fetchMarket,
  fetchPortfolio,
  login,
  marketControl,
  resetAdminUserPassword,
  submitOrder,
  updateAdminUserStatus
} from "../lib/api";
import type { AdminStock, AdminUser, AuditLog, Candle, MarketSnapshot, PortfolioSnapshot, StockQuote, UserSession } from "../lib/types";
import { KlineChart } from "./KlineChart";

type ViewKey = "market" | "trade" | "portfolio" | "records" | "admin";

function fmtMoney(value: number) {
  return `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
}

function cls(value: number) {
  return value >= 0 ? "up" : "down";
}

function textField(row: Record<string, unknown>, key: string) {
  const value = row[key];
  return value === null || value === undefined ? "-" : String(value);
}

function numberField(row: Record<string, unknown>, key: string) {
  const value = Number(row[key] ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function tradeSide(value: string) {
  if (value === "buy") return "买入";
  if (value === "sell") return "卖出";
  if (value === "force_close") return "强平";
  return value || "-";
}

export function TradingWorkspace() {
  const [market, setMarket] = useState<MarketSnapshot | null>(null);
  const [view, setView] = useState<ViewKey>("market");
  const [selected, setSelected] = useState("JGONG");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [token, setToken] = useState("");
  const [user, setUser] = useState<UserSession | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot | null>(null);
  const [loginName, setLoginName] = useState("player1");
  const [loginPassword, setLoginPassword] = useState("player1");
  const [loginError, setLoginError] = useState("");
  const [orderPrice, setOrderPrice] = useState("0.00");
  const [orderShares, setOrderShares] = useState("100");
  const [orderMessage, setOrderMessage] = useState("");
  const [adminMessage, setAdminMessage] = useState("");
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
  const [adminStocks, setAdminStocks] = useState<AdminStock[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [newOperatorName, setNewOperatorName] = useState("");
  const [newOperatorPassword, setNewOperatorPassword] = useState("");
  const [resetTarget, setResetTarget] = useState("");
  const [resetPassword, setResetPassword] = useState("");

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

  async function refreshAdminOverview() {
    if (!token || user?.role !== "admin") return;
    fetchAdminOverview(token)
      .then((data) => {
        setAdminUsers(data.users);
        setAdminStocks(data.stocks);
        setAuditLogs(data.auditLogs);
      })
      .catch(() => {
        setAdminUsers([]);
        setAdminStocks([]);
        setAuditLogs([]);
      });
  }

  useEffect(() => {
    if (!token || user?.role !== "admin") return;
    let alive = true;
    fetchAdminOverview(token)
      .then((data) => {
        if (!alive) return;
        setAdminUsers(data.users);
        setAdminStocks(data.stocks);
        setAuditLogs(data.auditLogs);
      })
      .catch(() => {
        if (!alive) return;
        setAdminUsers([]);
        setAdminStocks([]);
        setAuditLogs([]);
      });
    return () => {
      alive = false;
    };
  }, [token, user?.role]);

  async function submitLogin() {
    setLoginError("");
    try {
      const data = await login(loginName.trim(), loginPassword);
      setToken(data.accessToken);
      setUser(data.user);
      setPortfolio(null);
      setView(data.user.role === "admin" ? "admin" : "trade");
    } catch {
      setLoginError("账号或密码不正确");
    }
  }

  const stocks = market?.stocks ?? [];
  const current: StockQuote | undefined = useMemo(
    () => stocks.find((s) => s.symbol === selected) ?? stocks[0],
    [selected, stocks]
  );

  useEffect(() => {
    if (current) setOrderPrice(current.price.toFixed(2));
  }, [current]);

  async function submitTrade() {
    if (!user || !current || !token) return;
    setOrderMessage("");
    try {
      const result = await submitOrder(token, {
        username: user.username,
        symbol: current.symbol,
        side,
        price: Number(orderPrice),
        shares: Number(orderShares)
      });
      setOrderMessage(result.detail || result.reason || "委托已提交");
      if (result.accepted) {
        const data = await fetchPortfolio(token);
        setPortfolio(data);
        setUser(data.user);
      }
    } catch {
      setOrderMessage("委托提交失败，请稍后重试");
    }
  }

  async function submitMarketAction(action: "open" | "close") {
    if (!token) return;
    setAdminMessage("");
    try {
      const result = await marketControl(token, action);
      setAdminMessage(result.detail || result.reason || "操作完成");
      const nextMarket = await fetchMarket();
      setMarket(nextMarket);
    } catch {
      setAdminMessage("市场控制失败，请检查管理员权限");
    }
  }

  async function submitCreateOperator() {
    if (!token || user?.role !== "admin") return;
    setAdminMessage("");
    try {
      await createAdminUser(token, {
        username: newOperatorName.trim(),
        password: newOperatorPassword,
        role: "player"
      });
      setNewOperatorName("");
      setNewOperatorPassword("");
      setAdminMessage("操作员账号已创建");
      await refreshAdminOverview();
    } catch {
      setAdminMessage("创建账号失败，请检查用户名是否已存在");
    }
  }

  async function submitResetPassword() {
    if (!token || user?.role !== "admin") return;
    setAdminMessage("");
    try {
      await resetAdminUserPassword(token, resetTarget, resetPassword);
      setResetPassword("");
      setAdminMessage("密码已重置");
      await refreshAdminOverview();
    } catch {
      setAdminMessage("重置密码失败，请检查账号和密码");
    }
  }

  async function submitUserStatus(username: string, status: "active" | "disabled") {
    if (!token || user?.role !== "admin") return;
    setAdminMessage("");
    try {
      await updateAdminUserStatus(token, username, status);
      setAdminMessage(status === "active" ? "账号已启用" : "账号已停用");
      await refreshAdminOverview();
    } catch {
      setAdminMessage("账号状态更新失败");
    }
  }

  const navItems = user
    ? [
        { key: "market" as const, label: "行情面板", icon: BarChart3 },
        { key: "trade" as const, label: "操作员交易台", icon: Activity },
        { key: "portfolio" as const, label: "持仓资产", icon: Wallet },
        { key: "records" as const, label: "委托记录", icon: ClipboardList },
        { key: "admin" as const, label: "管理员控制台", icon: Shield }
      ]
    : [
        { key: "market" as const, label: "行情面板", icon: BarChart3 }
      ];

  function renderNav(className: string) {
    return (
      <div className={className}>
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <button
              className={view === item.key ? "active" : ""}
              key={item.key}
              onClick={() => setView(item.key)}
            >
              <Icon size={17} /> {item.label}
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <strong>Gipfel</strong>
          <span>Gipfel 投资竞赛平台</span>
        </div>
        {renderNav("nav")}
      </aside>

      <main className="main">
        <div className="topbar">
          <div>
            <div className="meta">Trading Arena</div>
            <strong>股票交易竞赛平台</strong>
          </div>
          {user ? (
            <button className="ghost" onClick={() => { setToken(""); setUser(null); setPortfolio(null); setView("market"); }}>
              {user.username} · 退出
            </button>
          ) : (
            <button className="ghost" onClick={() => setView("trade")}>操作员入口</button>
          )}
        </div>

        <section className="status-strip">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span className="status-dot" />
            <div>
              <strong>第 {market?.round ?? 1} 轮 · {market?.state === "closed" ? "已闭市" : "交易中"}</strong>
              <div className="meta">行情给选手研判 · 操作员登录代为下单 · 管理员维护账号</div>
            </div>
          </div>
          <div className="meta">PostgreSQL API Ready</div>
        </section>

        {(view === "market" || view === "trade") ? (
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
        ) : null}

        {(view === "market" || view === "trade") ? (
          <section className={`workspace ${view === "market" ? "market-only" : ""}`}>
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

            {view === "trade" ? (
            <aside className="ticket">
            <h2>操作员交易</h2>
            {!user && (
              <div className="login-box">
                <div className="field">
                  <label>操作员账号</label>
                  <input value={loginName} onChange={(e) => setLoginName(e.target.value)} />
                </div>
                <div className="field">
                  <label>密码</label>
                  <input type="password" value={loginPassword} onChange={(e) => setLoginPassword(e.target.value)} />
                </div>
                <button className="primary" onClick={submitLogin}>操作员登录</button>
                {loginError && <div className="error-text">{loginError}</div>}
              </div>
            )}
            {user && (
              <div className="account-box">
                <div className="row"><span>操作员</span><strong>{user.username}</strong></div>
                <div className="row"><span>角色</span><strong>{user.role === "admin" ? "管理员" : "操作员"}</strong></div>
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
                <input value={orderPrice} onChange={(e) => setOrderPrice(e.target.value)} />
              </div>
              <div className="field">
                <label>委托数量</label>
                <input value={orderShares} onChange={(e) => setOrderShares(e.target.value)} />
              </div>
              <button className="primary" disabled={!user} onClick={submitTrade}>{side === "buy" ? "提交买入" : "提交卖出"}</button>
              {orderMessage && <div className="hint-text">{orderMessage}</div>}
            </div>

            <div className="mini-table">
              <div className="row"><span>当前价格</span><strong>{current ? fmtMoney(current.price) : "--"}</strong></div>
              <div className="row"><span>委托模式</span><strong>限价撮合</strong></div>
              <div className="row"><span>数据源</span><strong>PostgreSQL 实时数据</strong></div>
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
            {portfolio?.orders.length ? (
              <div className="record-list">
                <div className="section-caption">最近委托</div>
                {portfolio.orders.slice(0, 4).map((order, idx) => {
                  const sideText = tradeSide(textField(order, "trade_type"));
                  const price = numberField(order, "price");
                  const shares = numberField(order, "shares");
                  return (
                    <div className="record-row" key={`${textField(order, "created_at")}-${idx}`}>
                      <div>
                        <strong>{textField(order, "stock_symbol")} · {sideText}</strong>
                        <span>第 {textField(order, "round")} 轮 · {shares} 股</span>
                      </div>
                      <div>{fmtMoney(price)}</div>
                    </div>
                  );
                })}
              </div>
            ) : null}
            {portfolio?.recentTrades.length ? (
              <div className="record-list">
                <div className="section-caption">最近成交</div>
                {portfolio.recentTrades.slice(0, 4).map((trade, idx) => {
                  const sideText = tradeSide(textField(trade, "trade_type"));
                  const price = numberField(trade, "price");
                  const shares = numberField(trade, "shares");
                  return (
                    <div className="record-row" key={`${textField(trade, "trade_date")}-${idx}`}>
                      <div>
                        <strong>{textField(trade, "stock_symbol")} · {sideText}</strong>
                        <span>第 {textField(trade, "round")} 轮 · {shares} 股</span>
                      </div>
                      <div>{fmtMoney(price * shares)}</div>
                    </div>
                  );
                })}
              </div>
            ) : null}
            {user?.role === "admin" ? (
              <div className="admin-box">
                <div className="section-caption">市场控制</div>
                <div className="admin-actions">
                  <button className="ghost" onClick={() => submitMarketAction("close")}>收盘结算</button>
                  <button className="ghost" onClick={() => submitMarketAction("open")}>开启下一轮</button>
                </div>
                {adminMessage && <div className="hint-text">{adminMessage}</div>}
                <div className="admin-stats">
                  <div><span>用户</span><strong>{adminUsers.length}</strong></div>
                  <div><span>股票/公司</span><strong>{adminStocks.filter((s) => !s.isDeleted).length}</strong></div>
                </div>
                {auditLogs.length ? (
                  <div className="audit-list">
                    <div className="section-caption">最近审计</div>
                    {auditLogs.slice(0, 5).map((log, idx) => (
                      <div className="audit-row" key={`${log.createdAt}-${idx}`}>
                        <span>{log.actor} · {log.action}</span>
                        <strong>{log.target || "-"}</strong>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
            </aside>
            ) : null}
          </section>
        ) : null}

        {view === "portfolio" ? (
          <section className="panel-grid">
            <div className="chart-card compact-panel">
              <div className="chart-head">
                <div>
                  <strong>持仓资产</strong>
                  <div className="meta">资金、持仓、市值和浮动盈亏</div>
                </div>
                <div className={cls(portfolio?.summary.totalPnl ?? 0)}>
                  {fmtMoney(portfolio?.summary.totalAssets ?? user?.balance ?? 0)}
                </div>
              </div>
              {!user ? (
                <div className="empty-state">请先登录交易账号查看资产。</div>
              ) : (
                <div className="asset-grid">
                  <div><span>可用资金</span><strong>{fmtMoney(portfolio?.user.balance ?? user.balance)}</strong></div>
                  <div><span>持仓市值</span><strong>{fmtMoney(portfolio?.summary.marketValue ?? 0)}</strong></div>
                  <div><span>总资产</span><strong>{fmtMoney(portfolio?.summary.totalAssets ?? user.balance)}</strong></div>
                  <div><span>浮动盈亏</span><strong className={cls(portfolio?.summary.totalPnl ?? 0)}>{fmtMoney(portfolio?.summary.totalPnl ?? 0)}</strong></div>
                </div>
              )}
              {portfolio?.positions.length ? (
                <div className="data-table">
                  <div className="table-row table-head"><span>公司</span><span>数量</span><span>成本</span><span>市值</span><span>盈亏</span></div>
                  {portfolio.positions.map((pos) => (
                    <div className="table-row" key={pos.symbol}>
                      <span>{pos.name}</span>
                      <span>{pos.shares}</span>
                      <span>{fmtMoney(pos.avgCost)}</span>
                      <span>{fmtMoney(pos.marketValue)}</span>
                      <span className={cls(pos.pnl)}>{fmtMoney(pos.pnl)}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </section>
        ) : null}

        {view === "records" ? (
          <section className="panel-grid two">
            <div className="chart-card compact-panel">
              <div className="chart-head"><strong>委托记录</strong><div className="meta">最近 20 条委托</div></div>
              {!portfolio?.orders.length ? <div className="empty-state">暂无委托记录。</div> : (
                <div className="data-table">
                  <div className="table-row table-head"><span>公司</span><span>方向</span><span>价格</span><span>数量</span><span>轮次</span></div>
                  {portfolio.orders.map((order, idx) => (
                    <div className="table-row" key={`${textField(order, "created_at")}-${idx}`}>
                      <span>{textField(order, "stock_symbol")}</span>
                      <span>{tradeSide(textField(order, "trade_type"))}</span>
                      <span>{fmtMoney(numberField(order, "price"))}</span>
                      <span>{numberField(order, "shares")}</span>
                      <span>{textField(order, "round")}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="chart-card compact-panel">
              <div className="chart-head"><strong>成交记录</strong><div className="meta">最近 20 条成交</div></div>
              {!portfolio?.recentTrades.length ? <div className="empty-state">暂无成交记录。</div> : (
                <div className="data-table">
                  <div className="table-row table-head"><span>公司</span><span>方向</span><span>价格</span><span>数量</span><span>轮次</span></div>
                  {portfolio.recentTrades.map((trade, idx) => (
                    <div className="table-row" key={`${textField(trade, "trade_date")}-${idx}`}>
                      <span>{textField(trade, "stock_symbol")}</span>
                      <span>{tradeSide(textField(trade, "trade_type"))}</span>
                      <span>{fmtMoney(numberField(trade, "price"))}</span>
                      <span>{numberField(trade, "shares")}</span>
                      <span>{textField(trade, "round")}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        ) : null}

        {view === "admin" ? (
          <section className="panel-grid">
            <div className="chart-card compact-panel">
              <div className="chart-head">
                <div>
                  <strong>管理控制</strong>
                  <div className="meta">市场轮次、操作员账号、公司股票和审计日志</div>
                </div>
                <div className="meta">{user?.role === "admin" ? "管理员已登录" : "需要管理员权限"}</div>
              </div>
              {user?.role !== "admin" ? (
                <div className="empty-state">请使用管理员账号登录后查看管理控制台。</div>
              ) : (
                <>
                  <div className="admin-actions wide">
                    <button className="ghost" onClick={() => submitMarketAction("close")}>收盘结算</button>
                    <button className="ghost" onClick={() => submitMarketAction("open")}>开启下一轮</button>
                  </div>
                  {adminMessage && <div className="hint-text">{adminMessage}</div>}
                  <div className="admin-stats wide">
                    <div><span>操作员/管理员</span><strong>{adminUsers.length}</strong></div>
                    <div><span>股票/公司</span><strong>{adminStocks.filter((s) => !s.isDeleted).length}</strong></div>
                  </div>
                  <div className="admin-form-grid">
                    <div className="management-panel">
                      <div className="section-caption">注册操作员账号</div>
                      <div className="inline-form">
                        <div className="field">
                          <label>用户名</label>
                          <input value={newOperatorName} onChange={(e) => setNewOperatorName(e.target.value)} placeholder="例如 player4" />
                        </div>
                        <div className="field">
                          <label>初始密码</label>
                          <input type="password" value={newOperatorPassword} onChange={(e) => setNewOperatorPassword(e.target.value)} placeholder="给操作员使用" />
                        </div>
                        <button className="primary" onClick={submitCreateOperator} disabled={!newOperatorName.trim() || !newOperatorPassword}>
                          创建账号
                        </button>
                      </div>
                    </div>
                    <div className="management-panel">
                      <div className="section-caption">重置账号密码</div>
                      <div className="inline-form">
                        <div className="field">
                          <label>账号</label>
                          <select value={resetTarget} onChange={(e) => setResetTarget(e.target.value)}>
                            <option value="">选择账号</option>
                            {adminUsers.map((account) => (
                              <option key={account.id} value={account.username}>{account.username}</option>
                            ))}
                          </select>
                        </div>
                        <div className="field">
                          <label>新密码</label>
                          <input type="password" value={resetPassword} onChange={(e) => setResetPassword(e.target.value)} />
                        </div>
                        <button className="primary" onClick={submitResetPassword} disabled={!resetTarget || !resetPassword}>
                          重置密码
                        </button>
                      </div>
                    </div>
                  </div>
                  <div className="management-grid">
                    <div className="management-panel">
                      <div className="section-caption">操作员账号</div>
                      <div className="data-table admin-table">
                        <div className="table-row user-col table-head"><span>用户名</span><span>角色</span><span>状态</span><span>余额</span><span>操作</span></div>
                        {adminUsers.map((account) => (
                          <div className="table-row user-col" key={account.id}>
                            <span>{account.username}</span>
                            <span>{account.role === "admin" ? "管理员" : "操作员"}</span>
                            <span className={account.status === "active" ? "up" : "down"}>{account.status === "active" ? "有效" : "停用"}</span>
                            <span>{fmtMoney(account.balance)}</span>
                            <span>
                              {account.role === "player" ? (
                                <button
                                  className="mini-action"
                                  onClick={() => submitUserStatus(account.username, account.status === "active" ? "disabled" : "active")}
                                >
                                  {account.status === "active" ? "停用" : "启用"}
                                </button>
                              ) : "-"}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className="management-panel">
                      <div className="section-caption">参赛公司股票</div>
                      <div className="data-table admin-table">
                        <div className="table-row stock-col table-head"><span>代码</span><span>公司</span><span>现价</span><span>营收</span><span>PE</span></div>
                        {adminStocks.map((stock) => (
                          <div className="table-row stock-col" key={stock.id}>
                            <span>{stock.symbol}</span>
                            <span>{stock.name}</span>
                            <span className={cls(stock.price - stock.previousClose)}>{fmtMoney(stock.price)}</span>
                            <span>{fmtMoney(stock.revenue)}</span>
                            <span>{stock.industryPe.toFixed(2)}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                  {auditLogs.length ? (
                    <div className="data-table">
                      <div className="table-row four-col table-head"><span>操作者</span><span>动作</span><span>对象</span><span>时间</span></div>
                      {auditLogs.map((log, idx) => (
                        <div className="table-row four-col" key={`${log.createdAt}-${idx}`}>
                          <span>{log.actor}</span>
                          <span>{log.action}</span>
                          <span>{log.target || "-"}</span>
                          <span>{log.createdAt}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </>
              )}
            </div>
          </section>
        ) : null}
      </main>

      {renderNav("mobile-nav")}
    </div>
  );
}
