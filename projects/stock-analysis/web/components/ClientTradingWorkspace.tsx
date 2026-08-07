"use client";

import dynamic from "next/dynamic";

const TradingWorkspace = dynamic(
  () => import("./TradingWorkspace").then((mod) => mod.TradingWorkspace),
  {
    ssr: false,
    loading: () => (
      <main className="main client-loading">
        <section className="status-strip">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span className="status-dot" style={{ background: "#F9C42F", animation: "pulse 1.5s ease-in-out infinite" }} />
            <strong>Gipfel 行情加载中…</strong>
          </div>
        </section>
        <div className="loading-spinner" style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "80px 24px",
          gap: 16
        }}>
          <div style={{
            width: 40,
            height: 40,
            border: "3px solid rgba(148,163,184,0.2)",
            borderTopColor: "#469FE6",
            borderRadius: "50%",
            animation: "spin 0.8s linear infinite"
          }} />
          <span style={{ color: "#94A3B8", fontSize: 14 }}>正在加载交易平台…</span>
        </div>
      </main>
    )
  }
);

export function ClientTradingWorkspace() {
  return <TradingWorkspace />;
}
