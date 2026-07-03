"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, BarChart3, ClipboardList, Shield, Wallet } from "lucide-react";
import {
  createAdminStock,
  createAdminUser,
  deleteAdminStock,
  deleteAdminUser,
  fetchAdminOverview,
  fetchCandles,
  fetchMarket,
  fetchPortfolio,
  login,
  marketControl,
  resetAdminUserPassword,
  submitOrder,
  updateAdminStock,
  updateAdminUserStatus
} from "../lib/api";
import type { AdminStock, AdminUser, AuditLog, Candle, MarketSnapshot, PortfolioSnapshot, StockQuote, UserSession } from "../lib/types";
import { KlineChart } from "./KlineChart";

type ViewKey = "market" | "trade" | "portfolio" | "records" | "admin";
type MarketAction = "open" | "close" | "reset";
type OrderStatus = "idle" | "success" | "error";

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

function calcInitialPrice(revenue: number, totalShares: number, industryPe: number) {
  if (!revenue || !totalShares || !industryPe) return 0;
  return Number((revenue * 10000 / totalShares / industryPe).toFixed(2));
}

function calcIndustryPe(revenue: number, totalShares: number, initialPrice: number) {
  if (!revenue || !totalShares || !initialPrice) return 0;
  return Number((revenue * 10000 / totalShares / initialPrice).toFixed(2));
}

function numericDraft(value: string) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
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
  const [loginName, setLoginName] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [orderPrice, setOrderPrice] = useState("0.00");
  const [orderShares, setOrderShares] = useState("100");
  const [orderMessage, setOrderMessage] = useState("");
  const [orderStatus, setOrderStatus] = useState<OrderStatus>("idle");
  const [orderSubmitting, setOrderSubmitting] = useState(false);
  const [adminMessage, setAdminMessage] = useState("");
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
  const [adminStocks, setAdminStocks] = useState<AdminStock[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [adminLoading, setAdminLoading] = useState(false);
  const [newOperatorName, setNewOperatorName] = useState("");
  const [newOperatorPassword, setNewOperatorPassword] = useState("");
  const [newOperatorRole, setNewOperatorRole] = useState<"player" | "admin">("player");
  const [resetTarget, setResetTarget] = useState("");
  const [resetPassword, setResetPassword] = useState("");
  const [pendingMarketAction, setPendingMarketAction] = useState<MarketAction | null>(null);
  const [marketConfirmText, setMarketConfirmText] = useState("");
  const [newStock, setNewStock] = useState({
    symbol: "",
    name: "",
    revenue: "100",
    totalShares: "10000",
    initialPrice: "5.00",
    carbonPrice: "50",
    industryCarbonMean: "50",
    premiumRate: "50"
  });
  const [stockDrafts, setStockDrafts] = useState<Record<string, {
    revenue: string;
    totalShares: string;
    initialPrice: string;
    carbonPrice: string;
    industryCarbonMean: string;
    premiumRate: string;
  }>>({});

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
    setAdminLoading(true);
    try {
      const data = await fetchAdminOverview(token);
      setAdminUsers(data.users);
      setAdminStocks(data.stocks);
      setAuditLogs(data.auditLogs);
    } catch {
      setAdminUsers([]);
      setAdminStocks([]);
      setAuditLogs([]);
    } finally {
      setAdminLoading(false);
    }
  }

  useEffect(() => {
    if (!token || user?.role !== "admin") return;
    let alive = true;
    setAdminLoading(true);
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
      })
      .finally(() => {
        if (alive) setAdminLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [token, user?.role]);

  useEffect(() => {
    setStockDrafts((currentDrafts) => {
      const next = { ...currentDrafts };
      for (const stock of adminStocks) {
        if (next[stock.symbol]) continue;
        next[stock.symbol] = {
          revenue: String(stock.revenue || 100),
          totalShares: String(stock.totalShares || 10000),
          initialPrice: String(calcInitialPrice(stock.revenue || 0, stock.totalShares || 0, stock.industryPe || 0) || stock.price || 0),
          carbonPrice: String(stock.carbonPrice || 50),
          industryCarbonMean: String(stock.industryCarbonMean || 50),
          premiumRate: String(stock.premiumRate || 50)
        };
      }
      return next;
    });
  }, [adminStocks]);

  async function submitLogin() {
    setLoginError("");
    try {
      const data = await login(loginName.trim(), loginPassword);
      setToken(data.accessToken);
      setUser(data.user);
      setPortfolio(null);
      setView(data.user.role === "admin" ? "admin" : "trade");
    } catch (error) {
      const detail = error instanceof Error ? error.message : "";
      setLoginError(detail === "invalid_credentials" ? "账号或密码不正确" : `登录失败：${detail || "请稍后重试"}`);
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
    const price = Number(orderPrice);
    const shares = Number(orderShares);
    if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(shares) || shares <= 0) {
      setOrderStatus("error");
      setOrderMessage("委托失败：请填写大于 0 的价格和数量。");
      return;
    }
    if (!Number.isInteger(shares)) {
      setOrderStatus("error");
      setOrderMessage("委托失败：委托数量必须是整数股。");
      return;
    }
    const normalizedShares = Math.floor(shares);
    const sideText = side === "buy" ? "买入" : "卖出";
    const confirmed = window.confirm(
      `确认${sideText} ${current.name} (${current.symbol})？\n\n委托价格：${fmtMoney(price)}\n委托数量：${normalizedShares} 股\n预计金额：${fmtMoney(price * normalizedShares)}\n\n提交后会进入撮合/成交流程。`
    );
    if (!confirmed) return;
    setOrderMessage("");
    setOrderStatus("idle");
    setOrderSubmitting(true);
    try {
      const result = await submitOrder(token, {
        username: user.username,
        symbol: current.symbol,
        side,
        price,
        shares: normalizedShares
      });
      if (result.accepted) {
        setOrderStatus("success");
        setOrderMessage(`${sideText}成功：${current.name} ${normalizedShares} 股，委托价 ${fmtMoney(price)}。${result.detail ? ` ${result.detail}` : "订单已受理，资产与行情已刷新。"}`);
        const data = await fetchPortfolio(token);
        setPortfolio(data);
        setUser(data.user);
        const nextMarket = await fetchMarket();
        setMarket(nextMarket);
        const nextCandles = await fetchCandles(current.symbol);
        setCandles(nextCandles);
      } else {
        setOrderStatus("error");
        setOrderMessage(`${sideText}失败：${result.detail || result.reason || "请检查市场状态、资金或持仓。"}`);
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : "";
      setOrderStatus("error");
      setOrderMessage(`${sideText}提交失败：${detail || "网络或后端暂时不可用，请稍后重试。"}`);
    } finally {
      setOrderSubmitting(false);
    }
  }

  async function submitMarketAction(action: MarketAction) {
    if (!token) return;
    const confirmation = action === "close" ? "confirm-close" : action === "open" ? "confirm-open" : "confirm-reset-round1";
    setAdminMessage("");
    try {
      const result = await marketControl(token, action, confirmation);
      setAdminMessage(result.detail || result.reason || "操作完成");
      const nextMarket = await fetchMarket();
      setMarket(nextMarket);
      setPendingMarketAction(null);
      setMarketConfirmText("");
    } catch (error) {
      const detail = error instanceof Error ? error.message : "";
      setAdminMessage(`市场控制失败：${detail || "请重新登录管理员账号"}`);
    }
  }

  async function submitCreateOperator() {
    if (!token || user?.role !== "admin") return;
    const roleText = newOperatorRole === "admin" ? "管理员" : "操作员";
    if (!window.confirm(`确认创建${roleText}账号「${newOperatorName.trim()}」？`)) return;
    setAdminMessage("");
    try {
      await createAdminUser(token, {
        username: newOperatorName.trim(),
        password: newOperatorPassword,
        role: newOperatorRole
      });
      setNewOperatorName("");
      setNewOperatorPassword("");
      setNewOperatorRole("player");
      setAdminMessage(`${roleText}账号已创建`);
      await refreshAdminOverview();
    } catch (error) {
      const detail = error instanceof Error ? error.message : "";
      setAdminMessage(`创建账号失败：${detail || "请检查用户名是否已存在"}`);
    }
  }

  async function submitResetPassword() {
    if (!token || user?.role !== "admin") return;
    if (!window.confirm(`确认重置账号「${resetTarget}」的密码？`)) return;
    setAdminMessage("");
    try {
      await resetAdminUserPassword(token, resetTarget, resetPassword);
      setResetPassword("");
      setAdminMessage("密码已重置");
      await refreshAdminOverview();
    } catch (error) {
      const detail = error instanceof Error ? error.message : "";
      setAdminMessage(`重置密码失败：${detail || "请检查账号和密码"}`);
    }
  }

  async function submitUserStatus(username: string, status: "active" | "disabled") {
    if (!token || user?.role !== "admin") return;
    const actionText = status === "active" ? "启用" : "停用";
    if (!window.confirm(`确认${actionText}操作员账号「${username}」？`)) return;
    setAdminMessage("");
    try {
      await updateAdminUserStatus(token, username, status);
      setAdminMessage(status === "active" ? "账号已启用" : "账号已停用");
      await refreshAdminOverview();
    } catch {
      setAdminMessage("账号状态更新失败");
    }
  }

  async function submitDeleteUser(username: string) {
    if (!token || user?.role !== "admin") return;
    if (!window.confirm(`确认删除操作员账号「${username}」？该账号的未成交挂单会一并移除。`)) return;
    setAdminMessage("");
    try {
      await deleteAdminUser(token, username);
      setAdminMessage("操作员账号已删除");
      await refreshAdminOverview();
    } catch (error) {
      const detail = error instanceof Error ? error.message : "";
      setAdminMessage(`删除账号失败：${detail || "请稍后重试"}`);
    }
  }

  async function submitCreateStock() {
    if (!token || user?.role !== "admin") return;
    const payload = {
      symbol: newStock.symbol.trim().toUpperCase(),
      name: newStock.name.trim(),
      revenue: numericDraft(newStock.revenue),
      total_shares: numericDraft(newStock.totalShares),
      industry_pe: calcIndustryPe(numericDraft(newStock.revenue), numericDraft(newStock.totalShares), numericDraft(newStock.initialPrice)),
      carbon_price: numericDraft(newStock.carbonPrice),
      industry_carbon_mean: numericDraft(newStock.industryCarbonMean),
      premium_rate: numericDraft(newStock.premiumRate)
    };
    if (!payload.symbol || !payload.name || payload.revenue <= 0 || payload.total_shares <= 0 || payload.industry_pe <= 0 || payload.industry_carbon_mean <= 0) {
      setAdminMessage("请完整填写股票代码、公司名称、净利润、总股本、目标初始价和碳排均值");
      return;
    }
    if (!window.confirm(`确认添加股票「${payload.name} (${payload.symbol})」？初始价 ${fmtMoney(newStockInitialPrice)}。`)) return;
    setAdminMessage("");
    try {
      const result = await createAdminStock(token, payload);
      if (!result.accepted) {
        setAdminMessage(result.detail || result.reason || "添加股票未生效");
        return;
      }
      setNewStock({
        symbol: "",
        name: "",
        revenue: "100",
        totalShares: "10000",
        initialPrice: "5.00",
        carbonPrice: "50",
        industryCarbonMean: "50",
        premiumRate: "50"
      });
      setAdminMessage(result.initialPrice ? `股票已添加，初始价 ${fmtMoney(result.initialPrice)}` : "股票已添加");
      await refreshAdminOverview();
      const nextMarket = await fetchMarket();
      setMarket(nextMarket);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "";
      setAdminMessage(`添加股票失败：${detail || "请检查代码是否重复或参数是否正确"}`);
    }
  }

  async function submitUpdateStock(stock: AdminStock) {
    if (!token || user?.role !== "admin") return;
    const draft = stockDrafts[stock.symbol];
    if (!draft) return;
    const payload = {
      revenue: numericDraft(draft.revenue),
      total_shares: numericDraft(draft.totalShares),
      industry_pe: calcIndustryPe(numericDraft(draft.revenue), numericDraft(draft.totalShares), numericDraft(draft.initialPrice)),
      carbon_price: numericDraft(draft.carbonPrice),
      industry_carbon_mean: numericDraft(draft.industryCarbonMean),
      premium_rate: numericDraft(draft.premiumRate)
    };
    if (payload.revenue <= 0 || payload.total_shares <= 0 || payload.industry_pe <= 0 || payload.industry_carbon_mean <= 0) {
      setAdminMessage("参数异常：净利润、总股本、目标初始价和碳排均值必须大于 0");
      return;
    }
    if (!window.confirm(`确认保存「${stock.name}」的股票参数？这会影响后续结算价格。`)) return;
    setAdminMessage("");
    try {
      const result = await updateAdminStock(token, stock.symbol, payload);
      if (!result.accepted) {
        setAdminMessage(result.detail || result.reason || "保存股票参数未生效");
        return;
      }
      setAdminMessage(`${stock.name} 参数已保存`);
      await refreshAdminOverview();
    } catch (error) {
      const detail = error instanceof Error ? error.message : "";
      setAdminMessage(`保存股票参数失败：${detail || "请稍后重试"}`);
    }
  }

  async function submitDeleteStock(stock: AdminStock) {
    if (!token || user?.role !== "admin") return;
    if (!window.confirm(`确认删除股票「${stock.name} (${stock.symbol})」？该股票会从行情和交易列表隐藏，历史记录保留。`)) return;
    setAdminMessage("");
    try {
      await deleteAdminStock(token, stock.symbol);
      setAdminMessage(`${stock.name} 已删除`);
      await refreshAdminOverview();
      const nextMarket = await fetchMarket();
      setMarket(nextMarket);
      if (nextMarket.stocks[0]) setSelected(nextMarket.stocks[0].symbol);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "";
      setAdminMessage(`删除股票失败：${detail || "请稍后重试"}`);
    }
  }

  const marketActionText = pendingMarketAction === "close" ? "收盘结算" : pendingMarketAction === "open" ? "开启下一轮" : "回到第一轮";
  const marketActionKeyword = pendingMarketAction === "close" ? "确认收盘" : pendingMarketAction === "open" ? "确认开盘" : "确认回到第一轮";
  const canCloseMarket = market?.state === "open";
  const canOpenMarket = market?.state === "closed";
  const newStockInitialPrice = numericDraft(newStock.initialPrice);
  const newStockIndustryPe = calcIndustryPe(
    numericDraft(newStock.revenue),
    numericDraft(newStock.totalShares),
    numericDraft(newStock.initialPrice)
  );

  const navItems = user
    ? [
        { key: "market" as const, label: "行情面板", icon: BarChart3 },
        { key: "trade" as const, label: "操作员交易台", icon: Activity },
        { key: "portfolio" as const, label: "持仓资产", icon: Wallet },
        { key: "records" as const, label: "委托记录", icon: ClipboardList },
        ...(user.role === "admin"
          ? [{ key: "admin" as const, label: "管理员控制台", icon: Shield }]
          : [])
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
            <strong>Gipfel 股票交易竞赛平台</strong>
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
            </div>
          </div>
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
                <div className="chart-title-stack">
                  <strong>{current?.name ?? "公司"} · {current?.symbol ?? "-"}</strong>
                </div>
                <div className={`chart-price-badge ${cls(current?.change ?? 0)}`}>
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
                <div className="section-caption">仅操作员和管理员登录后可下单。选手请留在行情面板查看走势。</div>
                <div className="field">
                  <label>操作员账号</label>
                  <input value={loginName} onChange={(e) => setLoginName(e.target.value)} placeholder="请输入账号" autoComplete="username" />
                </div>
                <div className="field">
                  <label>密码</label>
                  <input type="password" value={loginPassword} onChange={(e) => setLoginPassword(e.target.value)} placeholder="请输入密码" autoComplete="current-password" />
                </div>
                <button className="primary" onClick={submitLogin} disabled={!loginName.trim() || !loginPassword}>操作员登录</button>
                {loginError && <div className="error-text">{loginError}</div>}
              </div>
            )}
            {user && (
              <>
              <div className="account-box">
                <div className="row"><span>操作员</span><strong>{user.username}</strong></div>
                <div className="row"><span>角色</span><strong>{user.role === "admin" ? "管理员" : "操作员"}</strong></div>
                <div className="row"><span>可用资金</span><strong>{fmtMoney(portfolio?.user.balance ?? user.balance)}</strong></div>
                <div className="row"><span>总资产</span><strong>{fmtMoney(portfolio?.summary.totalAssets ?? user.balance)}</strong></div>
                <div className="row"><span>浮动盈亏</span><strong className={cls(portfolio?.summary.totalPnl ?? 0)}>{fmtMoney(portfolio?.summary.totalPnl ?? 0)}</strong></div>
              </div>
            <div className="segmented">
              <button
                className={side === "buy" ? "buy active" : ""}
                onClick={() => {
                  setSide("buy");
                  setOrderMessage("");
                  setOrderStatus("idle");
                }}
              >
                买入
              </button>
              <button
                className={side === "sell" ? "sell active" : ""}
                onClick={() => {
                  setSide("sell");
                  setOrderMessage("");
                  setOrderStatus("idle");
                }}
              >
                卖出
              </button>
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
              <button
                className={`primary trade-submit ${side}`}
                disabled={!user || orderSubmitting}
                onClick={submitTrade}
              >
                {orderSubmitting ? "提交中..." : side === "buy" ? "提交买入" : "提交卖出"}
              </button>
              {orderMessage && <div className={`order-result ${orderStatus}`}>{orderMessage}</div>}
            </div>

            <div className="mini-table">
              <div className="row"><span>当前价格</span><strong>{current ? fmtMoney(current.price) : "--"}</strong></div>
              <div className="row"><span>委托模式</span><strong>限价撮合</strong></div>
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
              </>
            )}
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
                <div className="admin-head-actions">
                  <span className="meta">{adminLoading ? "正在同步" : "管理员已登录"}</span>
                  {user?.role === "admin" ? (
                    <button className="mini-action" onClick={refreshAdminOverview}>刷新</button>
                  ) : null}
                </div>
              </div>
              {user?.role !== "admin" ? (
                <div className="empty-state">请使用管理员账号登录后查看管理控制台。</div>
              ) : (
                <>
                  <div className="danger-zone">
                    <div className="danger-copy">
                      <strong>市场轮次控制</strong>
                      <span>当前第 {market?.round ?? 1} 轮，状态：{market?.state === "closed" ? "已闭市" : "交易中"}。回到第一轮会清空成交、挂单、K线和持仓。</span>
                    </div>
                    <div className="admin-actions">
                      <button className="danger-button" disabled={!canCloseMarket} onClick={() => { setPendingMarketAction("close"); setMarketConfirmText(""); }}>
                        收盘结算
                      </button>
                      <button className="ghost" disabled={!canOpenMarket} onClick={() => { setPendingMarketAction("open"); setMarketConfirmText(""); }}>
                        开启下一轮
                      </button>
                      <button className="danger-button" onClick={() => { setPendingMarketAction("reset"); setMarketConfirmText(""); }}>
                        回到第一轮
                      </button>
                    </div>
                    {pendingMarketAction ? (
                      <div className="confirm-box">
                        <div>
                          <strong>确认执行：{marketActionText}</strong>
                          <span>请输入「{marketActionKeyword}」后才能继续。</span>
                          {pendingMarketAction === "reset" ? <span>该操作会清空比赛交易历史并恢复第 1 轮。</span> : null}
                        </div>
                        <input
                          value={marketConfirmText}
                          onChange={(e) => setMarketConfirmText(e.target.value)}
                          placeholder={marketActionKeyword}
                        />
                        <div className="confirm-actions">
                          <button className="ghost" onClick={() => { setPendingMarketAction(null); setMarketConfirmText(""); }}>取消</button>
                          <button
                            className="danger-button"
                            disabled={marketConfirmText.trim() !== marketActionKeyword}
                            onClick={() => submitMarketAction(pendingMarketAction)}
                          >
                            确认执行
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </div>
                  {adminMessage && <div className="hint-text">{adminMessage}</div>}
                  <div className="admin-stats wide">
                    <div><span>操作员/管理员</span><strong>{adminLoading ? "同步中" : adminUsers.length}</strong></div>
                    <div><span>股票/公司</span><strong>{adminLoading ? "同步中" : adminStocks.filter((s) => !s.isDeleted).length}</strong></div>
                  </div>
                  <div className="admin-form-grid">
                    <div className="management-panel">
                      <div className="section-caption">注册账号</div>
                      <div className="inline-form">
                        <div className="field">
                          <label>用户名</label>
                          <input value={newOperatorName} onChange={(e) => setNewOperatorName(e.target.value)} placeholder="例如 player4" />
                        </div>
                        <div className="field">
                          <label>账号角色</label>
                          <select value={newOperatorRole} onChange={(e) => setNewOperatorRole(e.target.value as "player" | "admin")}>
                            <option value="player">操作员</option>
                            <option value="admin">管理员</option>
                          </select>
                        </div>
                        <div className="field">
                          <label>初始密码</label>
                          <input type="password" value={newOperatorPassword} onChange={(e) => setNewOperatorPassword(e.target.value)} placeholder="给新账号使用" />
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
                  <div className="management-panel stock-create-panel">
                    <div className="section-caption">添加股票</div>
                    <div className="inline-form stock-form">
                      <div className="field">
                        <label>股票代码</label>
                        <input value={newStock.symbol} onChange={(e) => setNewStock({ ...newStock, symbol: e.target.value.toUpperCase() })} placeholder="例如 NEWCO" />
                      </div>
                      <div className="field">
                        <label>公司名称</label>
                        <input value={newStock.name} onChange={(e) => setNewStock({ ...newStock, name: e.target.value })} placeholder="公司/小组名称" />
                      </div>
                      <div className="field">
                        <label>总股本（万股）</label>
                        <input value={newStock.totalShares} onChange={(e) => setNewStock({ ...newStock, totalShares: e.target.value })} />
                      </div>
                      <div className="field">
                        <label>初始净利润（万）</label>
                        <input value={newStock.revenue} onChange={(e) => setNewStock({ ...newStock, revenue: e.target.value })} />
                      </div>
                      <div className="field">
                        <label>目标初始价</label>
                        <input value={newStock.initialPrice} onChange={(e) => setNewStock({ ...newStock, initialPrice: e.target.value })} />
                      </div>
                      <div className="field">
                        <label>当前幸福度</label>
                        <input value={newStock.premiumRate} onChange={(e) => setNewStock({ ...newStock, premiumRate: e.target.value })} />
                      </div>
                      <div className="field">
                        <label>当前碳排</label>
                        <input value={newStock.carbonPrice} onChange={(e) => setNewStock({ ...newStock, carbonPrice: e.target.value })} />
                      </div>
                      <div className="field">
                        <label>行业碳排均值</label>
                        <input value={newStock.industryCarbonMean} onChange={(e) => setNewStock({ ...newStock, industryCarbonMean: e.target.value })} />
                      </div>
                      <div className="formula-preview">
                        <span>反推行业PE</span>
                        <strong>{newStockIndustryPe > 0 ? newStockIndustryPe.toFixed(2) : "--"}</strong>
                      </div>
                      <button
                        className="primary"
                        onClick={submitCreateStock}
                        disabled={!newStock.symbol.trim() || !newStock.name.trim() || newStockInitialPrice <= 0}
                      >
                        添加股票
                      </button>
                    </div>
                  </div>
                  <div className="management-grid">
                    <div className="management-panel user-management-panel">
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
                                <span className="row-actions">
                                  <button
                                    className="mini-action"
                                    onClick={() => submitUserStatus(account.username, account.status === "active" ? "disabled" : "active")}
                                  >
                                    {account.status === "active" ? "停用" : "启用"}
                                  </button>
                                  <button className="mini-action danger-mini" onClick={() => submitDeleteUser(account.username)}>
                                    删除
                                  </button>
                                </span>
                              ) : "-"}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className="management-panel">
                      <div className="section-caption">股票参数</div>
                      <div className="stock-editor-list">
                        {adminStocks.map((stock) => {
                          const draft = stockDrafts[stock.symbol] ?? {
                            revenue: String(stock.revenue || 100),
                            totalShares: String(stock.totalShares || 10000),
                            initialPrice: String(calcInitialPrice(stock.revenue || 0, stock.totalShares || 0, stock.industryPe || 0) || stock.price || 0),
                            carbonPrice: String(stock.carbonPrice || 50),
                            industryCarbonMean: String(stock.industryCarbonMean || 50),
                            premiumRate: String(stock.premiumRate || 50)
                          };
                          const derivedPe = calcIndustryPe(
                            numericDraft(draft.revenue),
                            numericDraft(draft.totalShares),
                            numericDraft(draft.initialPrice)
                          );
                          const initialPrice = calcInitialPrice(
                            numericDraft(draft.revenue),
                            numericDraft(draft.totalShares),
                            derivedPe
                          );
                          const happinessFactor = 1 + 0.2 * (numericDraft(draft.premiumRate) - 50) / 50;
                          const carbonMean = Math.max(numericDraft(draft.industryCarbonMean), 1);
                          const carbonFactor = 1 - 0.5 * (numericDraft(draft.carbonPrice) - carbonMean) / carbonMean;
                          const updateDraft = (next: Partial<typeof draft>) => {
                            setStockDrafts((currentDrafts) => ({
                              ...currentDrafts,
                              [stock.symbol]: { ...(currentDrafts[stock.symbol] ?? draft), ...next }
                            }));
                          };
                          return (
                            <div className="stock-editor-card" key={stock.id}>
                              <div className="stock-editor-head">
                                <div>
                                  <strong>{stock.name}</strong>
                                  <span>{stock.symbol}</span>
                                </div>
                                <div className={cls(stock.price - stock.previousClose)}>{fmtMoney(stock.price)}</div>
                              </div>
                              <div className="stock-param-grid">
                                <div className="field"><label>总股本（万股）</label><input value={draft.totalShares} onChange={(e) => updateDraft({ totalShares: e.target.value })} /></div>
                                <div className="field"><label>初始净利润（万）</label><input value={draft.revenue} onChange={(e) => updateDraft({ revenue: e.target.value })} /></div>
                                <div className="field"><label>目标初始价</label><input value={draft.initialPrice} onChange={(e) => updateDraft({ initialPrice: e.target.value })} /></div>
                                <div className="field"><label>幸福度</label><input value={draft.premiumRate} onChange={(e) => updateDraft({ premiumRate: e.target.value })} /></div>
                                <div className="field"><label>当前碳排</label><input value={draft.carbonPrice} onChange={(e) => updateDraft({ carbonPrice: e.target.value })} /></div>
                                <div className="field"><label>行业碳排均值</label><input value={draft.industryCarbonMean} onChange={(e) => updateDraft({ industryCarbonMean: e.target.value })} /></div>
                              </div>
                              <div className="factor-strip">
                                <span>初始价 {fmtMoney(initialPrice)}</span>
                                <span>PE {derivedPe > 0 ? derivedPe.toFixed(2) : "--"}</span>
                                <span>幸福因子 {happinessFactor.toFixed(3)}</span>
                                <span>碳因子 {carbonFactor.toFixed(3)}</span>
                              </div>
                              <div className="row-actions">
                                <button className="mini-action" onClick={() => submitUpdateStock(stock)}>保存参数</button>
                                <button className="mini-action danger-mini" onClick={() => submitDeleteStock(stock)}>删除股票</button>
                              </div>
                            </div>
                          );
                        })}
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
