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
            <span className="status-dot" />
            <strong>Gipfel 行情加载中</strong>
          </div>
        </section>
      </main>
    )
  }
);

export function ClientTradingWorkspace() {
  return <TradingWorkspace />;
}
