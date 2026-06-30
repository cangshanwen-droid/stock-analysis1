import base64
import hashlib
import hmac
import json
import os
import time
from datetime import date, timedelta
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .db import DB_PATH, DatabaseNotReady, connect, fetchall, fetchone, is_postgres, row_dict
from .trading import place_order

TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "change-me-before-production")
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "28800"))
ENABLE_ORDER_WRITES = os.environ.get("ENABLE_ORDER_WRITES", "false").lower() == "true"

app = FastAPI(title="Gipfel Trading API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DatabaseNotReady)
def database_not_ready_handler(_, exc: DatabaseNotReady):
    return JSONResponse(status_code=503, content={"detail": "database_not_ready", "message": str(exc)})


class TradeRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    symbol: str = Field(min_length=1, max_length=16)
    side: str = Field(pattern="^(buy|sell)$")
    price: float = Field(gt=0)
    shares: int = Field(gt=0)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=120)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64url(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def hash_pwd(password: str, salt: str = "") -> str:
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


def check_pwd(stored: str, plain: str) -> bool:
    if ":" in stored:
        salt, digest = stored.split(":", 1)
        return hmac.compare_digest(hash_pwd(plain, salt), digest)
    return hmac.compare_digest(hash_pwd(plain), stored)


def sign_token(payload: dict[str, Any]) -> str:
    body = b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = b64url(hmac.new(TOKEN_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str) -> dict[str, Any]:
    try:
        body, sig = token.split(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid_token") from exc
    expected = b64url(hmac.new(TOKEN_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="invalid_token")
    payload = json.loads(unb64url(body).decode("utf-8"))
    if int(payload.get("exp") or 0) < int(time.time()):
        raise HTTPException(status_code=401, detail="token_expired")
    return payload


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing_token")
    payload = verify_token(authorization.removeprefix("Bearer ").strip())
    with connect() as conn:
        user = row_dict(fetchone(conn,
            "SELECT username,role,status,balance FROM users WHERE username=?",
            (payload.get("sub"),),
        ))
    if not user or user.get("status") == "disabled":
        raise HTTPException(status_code=401, detail="inactive_user")
    return user


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "database": True if is_postgres() else DB_PATH.exists(),
        "backend": "postgres" if is_postgres() else "sqlite",
        "path": "" if is_postgres() else str(DB_PATH),
        "tokenSecretConfigured": TOKEN_SECRET != "change-me-before-production",
        "orderWritesEnabled": ENABLE_ORDER_WRITES,
    }


@app.post("/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    with connect() as conn:
        user = row_dict(fetchone(conn,
            "SELECT username,password,role,status,balance FROM users WHERE username=?",
            (payload.username,),
        ))
    if not user or not check_pwd(str(user["password"]), payload.password):
        raise HTTPException(status_code=401, detail="invalid_credentials")
    if user.get("status") == "disabled":
        raise HTTPException(status_code=403, detail="user_disabled")
    now = int(time.time())
    token = sign_token({"sub": user["username"], "role": user["role"], "iat": now, "exp": now + TOKEN_TTL_SECONDS})
    return {
        "accessToken": token,
        "tokenType": "bearer",
        "expiresIn": TOKEN_TTL_SECONDS,
        "user": {
            "username": user["username"],
            "role": user["role"],
            "balance": float(user["balance"] or 0),
        },
    }


@app.get("/auth/me")
def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {
        "username": user["username"],
        "role": user["role"],
        "balance": float(user["balance"] or 0),
    }


@app.get("/market")
def market() -> dict[str, Any]:
    with connect() as conn:
        state = row_dict(fetchone(conn, "SELECT state, round FROM market_state WHERE id=1"))
        stocks = fetchall(conn,
            "SELECT symbol,name,current_price,previous_close FROM stocks WHERE is_deleted=0 ORDER BY symbol"
        )
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
    with connect() as conn:
        rows = fetchall(conn,
            """
            SELECT round,open_price,high_price,low_price,close_price,volume,created_at
            FROM kline
            WHERE stock_symbol=?
            ORDER BY round
            """,
            (symbol.upper(),),
        )
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


@app.get("/portfolio")
def portfolio(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    username = user["username"]
    with connect() as conn:
        stocks = {
            row["symbol"]: row_dict(row)
            for row in fetchall(conn, "SELECT symbol,name,current_price FROM stocks WHERE is_deleted=0")
        }
        buys = fetchall(conn,
            """
            SELECT stock_symbol,SUM(shares) AS shares,SUM(price*shares) AS cost
            FROM transactions
            WHERE username=? AND trade_type='buy'
            GROUP BY stock_symbol
            """,
            (username,),
        )
        sells = fetchall(conn,
            """
            SELECT stock_symbol,SUM(shares) AS shares
            FROM transactions
            WHERE username=? AND trade_type IN ('sell','force_close')
            GROUP BY stock_symbol
            """,
            (username,),
        )
        orders = fetchall(conn,
            """
            SELECT stock_symbol,trade_type,price,shares,round,created_at
            FROM order_book
            WHERE username=?
            ORDER BY id DESC
            LIMIT 20
            """,
            (username,),
        )
        recent = fetchall(conn,
            """
            SELECT stock_symbol,trade_type,price,shares,round,trade_date
            FROM transactions
            WHERE username=?
            ORDER BY id DESC
            LIMIT 20
            """,
            (username,),
        )
    sold = {row["stock_symbol"]: row["shares"] or 0 for row in sells}
    positions = []
    total_market_value = 0.0
    total_cost = 0.0
    for row in buys:
        symbol = row["stock_symbol"]
        shares = float(row["shares"] or 0) - float(sold.get(symbol, 0) or 0)
        if shares <= 0:
            continue
        stock = stocks.get(symbol) or {"symbol": symbol, "name": symbol, "current_price": 0}
        cost = float(row["cost"] or 0)
        avg_cost = cost / float(row["shares"] or 1)
        current_price = float(stock.get("current_price") or avg_cost)
        market_value = current_price * shares
        pnl = market_value - avg_cost * shares
        total_market_value += market_value
        total_cost += avg_cost * shares
        positions.append({
            "symbol": symbol,
            "name": stock.get("name") or symbol,
            "shares": int(shares),
            "avgCost": round(avg_cost, 2),
            "currentPrice": round(current_price, 2),
            "marketValue": round(market_value, 2),
            "pnl": round(pnl, 2),
            "pnlRatio": round(pnl / (avg_cost * shares) * 100, 2) if avg_cost and shares else 0,
        })
    total_assets = float(user["balance"] or 0) + total_market_value
    total_pnl = total_market_value - total_cost
    return {
        "user": {"username": username, "role": user["role"], "balance": float(user["balance"] or 0)},
        "summary": {
            "marketValue": round(total_market_value, 2),
            "totalAssets": round(total_assets, 2),
            "totalPnl": round(total_pnl, 2),
            "pnlRatio": round(total_pnl / total_cost * 100, 2) if total_cost else 0,
        },
        "positions": positions,
        "orders": [dict(row) for row in orders],
        "recentTrades": [dict(row) for row in recent],
    }


@app.post("/orders")
def create_order(payload: TradeRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if payload.username != user["username"] and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="cannot_trade_for_other_user")
    if not ENABLE_ORDER_WRITES:
        return {
            "accepted": False,
            "reason": "order_api_not_enabled_yet",
            "detail": "Set ENABLE_ORDER_WRITES=true only after migration tests pass.",
            "order": payload.model_dump(),
        }
    with connect() as conn:
        result = place_order(conn, payload.username, payload.symbol, payload.side, payload.price, payload.shares)
        if result.ok:
            conn.commit()
        else:
            conn.rollback()
    return {
        "accepted": result.ok,
        "reason": "" if result.ok else "order_rejected",
        "detail": result.message,
        "matched": result.matched,
        "round": result.round,
        "order": payload.model_dump(),
    }
