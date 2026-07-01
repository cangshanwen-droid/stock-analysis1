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

from .db import DB_PATH, DatabaseNotReady, connect, execute, fetchall, fetchone, is_postgres, row_dict
from .market_ops import close_market, open_market
from .trading import place_order

TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "change-me-before-production")
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "28800"))
ENABLE_ORDER_WRITES = os.environ.get("ENABLE_ORDER_WRITES", "false").lower() == "true"
ENABLE_MARKET_WRITES = os.environ.get("ENABLE_MARKET_WRITES", "false").lower() == "true"
ENABLE_ADMIN_WRITES = os.environ.get("ENABLE_ADMIN_WRITES", "false").lower() == "true"

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


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "Gipfel Trading API", "status": "ok", "health": "/health"}


class TradeRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    symbol: str = Field(min_length=1, max_length=16)
    side: str = Field(pattern="^(buy|sell)$")
    price: float = Field(gt=0)
    shares: int = Field(gt=0)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=120)


class UserStatusRequest(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=1, max_length=120)


class StockUpdateRequest(BaseModel):
    revenue: float | None = Field(default=None, gt=0)
    total_shares: float | None = Field(default=None, gt=0)
    industry_pe: float | None = Field(default=None, gt=0)
    carbon_price: float | None = None
    industry_carbon_mean: float | None = Field(default=None, gt=0)
    premium_rate: float | None = None


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64url(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def hash_pwd(password: str, salt: str = "") -> str:
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


def make_pwd(password: str) -> str:
    salt = b64url(os.urandom(8))
    return f"{salt}:{hash_pwd(password, salt)}"


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
        "marketWritesEnabled": ENABLE_MARKET_WRITES,
        "adminWritesEnabled": ENABLE_ADMIN_WRITES,
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


def require_admin(user: dict[str, Any]) -> None:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="admin_required")


@app.post("/admin/market/close")
def close_market_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    if not ENABLE_MARKET_WRITES:
        return {
            "accepted": False,
            "reason": "market_api_not_enabled_yet",
            "detail": "Set ENABLE_MARKET_WRITES=true only after settlement tests pass.",
        }
    with connect() as conn:
        result = close_market(conn)
        if result.ok:
            conn.commit()
        else:
            conn.rollback()
    return {
        "accepted": result.ok,
        "detail": result.message,
        "round": result.round,
        "settledStocks": result.settled_stocks,
        "matchedShares": result.matched_shares,
    }


@app.post("/admin/market/open")
def open_market_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    if not ENABLE_MARKET_WRITES:
        return {
            "accepted": False,
            "reason": "market_api_not_enabled_yet",
            "detail": "Set ENABLE_MARKET_WRITES=true only after settlement tests pass.",
        }
    with connect() as conn:
        result = open_market(conn)
        if result.ok:
            conn.commit()
        else:
            conn.rollback()
    return {
        "accepted": result.ok,
        "detail": result.message,
        "round": result.round,
        "settledStocks": result.settled_stocks,
        "matchedShares": result.matched_shares,
    }


@app.get("/admin/users")
def admin_users(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    require_admin(user)
    with connect() as conn:
        rows = fetchall(conn, """
            SELECT id,username,role,status,balance,created_at
            FROM users
            ORDER BY id
        """)
    return [
        {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "status": row["status"],
            "balance": float(row["balance"] or 0),
            "createdAt": str(row["created_at"]),
        }
        for row in rows
    ]


@app.patch("/admin/users/{username}/status")
def admin_update_user_status(username: str, payload: UserStatusRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    if username == user["username"]:
        raise HTTPException(status_code=400, detail="cannot_disable_self")
    if not ENABLE_ADMIN_WRITES:
        return {"accepted": False, "reason": "admin_api_not_enabled_yet", "detail": "Set ENABLE_ADMIN_WRITES=true after admin tests pass."}
    with connect() as conn:
        execute_user = fetchone(conn, "SELECT username FROM users WHERE username=? AND role='player'", (username,))
        if not execute_user:
            raise HTTPException(status_code=404, detail="user_not_found")
        execute(conn, "UPDATE users SET status=? WHERE username=? AND role='player'", (payload.status, username))
        execute(conn, "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                (user["username"], "user_status", username, payload.status))
        conn.commit()
    return {"accepted": True, "username": username, "status": payload.status}


@app.patch("/admin/users/{username}/password")
def admin_reset_user_password(username: str, payload: PasswordResetRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    if not ENABLE_ADMIN_WRITES:
        return {"accepted": False, "reason": "admin_api_not_enabled_yet", "detail": "Set ENABLE_ADMIN_WRITES=true after admin tests pass."}
    with connect() as conn:
        target = fetchone(conn, "SELECT username FROM users WHERE username=?", (username,))
        if not target:
            raise HTTPException(status_code=404, detail="user_not_found")
        execute(conn, "UPDATE users SET password=? WHERE username=?", (make_pwd(payload.password), username))
        execute(conn, "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                (user["username"], "reset_password", username, "password reset from web api"))
        conn.commit()
    return {"accepted": True, "username": username}


@app.get("/admin/stocks")
def admin_stocks(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    require_admin(user)
    with connect() as conn:
        rows = fetchall(conn, """
            SELECT id,symbol,name,current_price,previous_close,is_deleted,total_shares,revenue,industry_pe,
                   carbon_price,industry_carbon_mean,premium_rate,init_funds,last_update
            FROM stocks
            ORDER BY symbol
        """)
    return [
        {
            "id": row["id"],
            "symbol": row["symbol"],
            "name": row["name"],
            "price": float(row["current_price"] or 0),
            "previousClose": float(row["previous_close"] or 0),
            "isDeleted": bool(row["is_deleted"]),
            "totalShares": float(row["total_shares"] or 0),
            "revenue": float(row["revenue"] or 0),
            "industryPe": float(row["industry_pe"] or 0),
            "carbonPrice": float(row["carbon_price"] or 0),
            "industryCarbonMean": float(row["industry_carbon_mean"] or 0),
            "premiumRate": float(row["premium_rate"] or 0),
            "initFunds": float(row["init_funds"] or 0),
            "lastUpdate": str(row["last_update"]),
        }
        for row in rows
    ]


@app.patch("/admin/stocks/{symbol}")
def admin_update_stock(symbol: str, payload: StockUpdateRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    safe = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    column_map = {
        "revenue": "revenue",
        "total_shares": "total_shares",
        "industry_pe": "industry_pe",
        "carbon_price": "carbon_price",
        "industry_carbon_mean": "industry_carbon_mean",
        "premium_rate": "premium_rate",
    }
    if not safe:
        return {"accepted": False, "reason": "no_fields"}
    if not ENABLE_ADMIN_WRITES:
        return {"accepted": False, "reason": "admin_api_not_enabled_yet", "detail": "Set ENABLE_ADMIN_WRITES=true after admin tests pass."}
    sets = ", ".join(f"{column_map[key]}=?" for key in safe)
    vals = tuple(safe.values()) + (symbol.upper(),)
    with connect() as conn:
        target = fetchone(conn, "SELECT symbol FROM stocks WHERE symbol=?", (symbol.upper(),))
        if not target:
            raise HTTPException(status_code=404, detail="stock_not_found")
        execute(conn, f"UPDATE stocks SET {sets} WHERE symbol=?", vals)
        execute(conn, "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                (user["username"], "update_stock", symbol.upper(), json.dumps(safe, ensure_ascii=False)))
        conn.commit()
    return {"accepted": True, "symbol": symbol.upper(), "updated": safe}


@app.get("/admin/audit-logs")
def admin_audit_logs(limit: int = 80, user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    require_admin(user)
    limit = min(max(limit, 1), 200)
    with connect() as conn:
        rows = fetchall(conn, """
            SELECT actor,action,target,detail,created_at
            FROM audit_logs
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
    return [
        {
            "actor": row["actor"],
            "action": row["action"],
            "target": row["target"],
            "detail": row["detail"],
            "createdAt": str(row["created_at"]),
        }
        for row in rows
    ]
