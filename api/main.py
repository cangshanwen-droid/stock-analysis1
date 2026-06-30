import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("SQLITE_DB_PATH", ROOT_DIR / "data" / "stock_analysis.db"))

app = FastAPI(title="Gipfel Trading API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TradeRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    symbol: str = Field(min_length=1, max_length=16)
    side: str = Field(pattern="^(buy|sell)$")
    price: float = Field(gt=0)
    shares: int = Field(gt=0)


def db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="database_not_ready")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "database": DB_PATH.exists(), "path": str(DB_PATH)}


@app.get("/market")
def market() -> dict[str, Any]:
    with db() as conn:
        state = row_dict(conn.execute("SELECT state, round FROM market_state WHERE id=1").fetchone())
        stocks = conn.execute(
            "SELECT symbol,name,current_price,previous_close FROM stocks WHERE is_deleted=0 ORDER BY symbol"
        ).fetchall()
    return {
        "round": int((state or {}).get("round") or 1),
        "state": (state or {}).get("state") or "open",
        "stocks": [
            {
                "symbol": s["symbol"],
                "name": s["name"],
                "price": float(s["current_price"] or 0),
                "change": float((s["current_price"] or 0) - (s["previous_close"] or s["current_price"] or 0)),
                "changePct": (
                    float(((s["current_price"] or 0) - (s["previous_close"] or s["current_price"] or 0))
                          / (s["previous_close"] or s["current_price"] or 1) * 100)
                ),
            }
            for s in stocks
        ],
    }


@app.get("/stocks/{symbol}/kline")
def stock_kline(symbol: str) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT round,open_price,high_price,low_price,close_price,volume,created_at
            FROM kline
            WHERE stock_symbol=?
            ORDER BY round
            """,
            (symbol.upper(),),
        ).fetchall()
    start = date(2026, 1, 1)
    return [
        {
            "time": (start + timedelta(days=int(row["round"] or 1) - 1)).isoformat(),
            "open": float(row["open_price"] or 0),
            "high": float(row["high_price"] or 0),
            "low": float(row["low_price"] or 0),
            "close": float(row["close_price"] or 0),
            "volume": int(row["volume"] or 0),
        }
        for row in rows
        if row["open_price"] and row["high_price"] and row["low_price"] and row["close_price"]
    ]


@app.post("/orders")
def create_order(payload: TradeRequest) -> dict[str, Any]:
    return {
        "accepted": False,
        "reason": "order_api_not_enabled_yet",
        "detail": "Trading writes will be enabled after auth, PostgreSQL, and settlement tests are migrated.",
        "order": payload.model_dump(),
    }
