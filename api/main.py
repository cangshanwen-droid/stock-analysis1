import base64
import hashlib
import hmac
import json
import os
import threading
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .db import DB_PATH, DatabaseNotReady, connect, execute, fetchall, fetchone, is_postgres, row_dict
from .market_ops import close_market, compute_price, is_system_user, open_market, reset_to_round1, rollback_previous_round
from .trading import place_order

TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "change-me-before-production")
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", "28800"))
ENABLE_ORDER_WRITES = os.environ.get("ENABLE_ORDER_WRITES", "false").lower() == "true"
ENABLE_MARKET_WRITES = os.environ.get("ENABLE_MARKET_WRITES", "false").lower() == "true"
ENABLE_ADMIN_WRITES = os.environ.get("ENABLE_ADMIN_WRITES", "false").lower() == "true"
DEFAULT_CORS_ORIGINS = {
    "https://stock-analysis1-ten.vercel.app",
    "https://www.gipfel.ltd",
    "https://gipfel.ltd",
}
READ_CACHE: dict[str, tuple[float, Any]] = {}
READ_CACHE_LOCK = threading.RLock()


def cors_origins() -> list[str]:
    configured = {
        origin.strip()
        for origin in os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",")
        if origin.strip()
    }
    if "*" in configured:
        return ["*"]
    return sorted(configured | DEFAULT_CORS_ORIGINS)


def cache_get(key: str, ttl_seconds: float) -> Any | None:
    with READ_CACHE_LOCK:
        cached = READ_CACHE.get(key)
        if not cached:
            return None
        expires_at, value = cached
        if expires_at <= time.monotonic():
            READ_CACHE.pop(key, None)
            return None
        return value


def cache_set(key: str, value: Any, ttl_seconds: float) -> Any:
    with READ_CACHE_LOCK:
        READ_CACHE[key] = (time.monotonic() + ttl_seconds, value)
        return value


def clear_read_cache() -> None:
    with READ_CACHE_LOCK:
        READ_CACHE.clear()


def cache_get_or_set(key: str, ttl_seconds: float, loader):
    cached = cache_get(key, ttl_seconds)
    if cached is not None:
        return cached
    with READ_CACHE_LOCK:
        cached = cache_get(key, ttl_seconds)
        if cached is not None:
            return cached
        return cache_set(key, loader(), ttl_seconds)

app = FastAPI(title="Gipfel Trading API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_public_read_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.method == "GET" and response.status_code == 200:
        path = request.url.path
        if path == "/market":
            response.headers["Cache-Control"] = "public, max-age=2, stale-while-revalidate=8"
        elif path.startswith("/stocks/") and path.endswith("/kline"):
            response.headers["Cache-Control"] = "public, max-age=2, stale-while-revalidate=8"
    return response


# Rate limiting — in-memory sliding window per IP
_RATE_WINDOW = 60.0
_RATE_LIMITS = {"read": 60000, "write": 3000, "login": 60}
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _rate_limit(ip: str, limit: int) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    cutoff = now - _RATE_WINDOW
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Skip rate limiting for admin endpoints and health
    path = request.url.path
    if path.startswith("/admin") or path in ("/", "/health"):
        return await call_next(request)
    client_ip = request.client.host if request.client else "unknown"
    if path == "/auth/login":
        limit = _RATE_LIMITS["login"]
    elif request.method in ("POST", "PATCH", "DELETE"):
        limit = _RATE_LIMITS["write"]
    else:
        limit = _RATE_LIMITS["read"]
    if not _rate_limit(client_ip, limit):
        return JSONResponse(status_code=429, content={"detail": "too_many_requests", "retry_after": 60})
    return await call_next(request)


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
    shares: int = Field(gt=0, le=1_000_000)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=120)


class UserStatusRequest(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=1, max_length=120)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=120)
    role: str = Field(default="player", pattern="^(player|admin)$")


class StockUpdateRequest(BaseModel):
    revenue: float | None = Field(default=None, gt=0, le=1_000_000_000)
    total_shares: float | None = Field(default=None, gt=0, le=1_000_000_000)
    industry_pe: float | None = Field(default=None, gt=0, le=1_000_000)
    carbon_price: float | None = Field(default=None, ge=0, le=1_000_000)
    industry_carbon_mean: float | None = Field(default=None, gt=0, le=1_000_000)
    premium_rate: float | None = Field(default=None, ge=0, le=100)


class MarketControlRequest(BaseModel):
    confirmation: str = ""


class CreateStockRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=80)
    revenue: float = Field(gt=0, le=1_000_000_000)
    total_shares: float = Field(gt=0, le=1_000_000_000)
    industry_pe: float = Field(gt=0, le=1_000_000)
    carbon_price: float = Field(default=50, ge=0, le=1_000_000)
    industry_carbon_mean: float = Field(default=50, gt=0, le=1_000_000)
    premium_rate: float = Field(default=50, ge=0, le=100)


def initial_stock_price(revenue: float, total_shares: float, industry_pe: float) -> float:
    return round(revenue * 10000 / total_shares / industry_pe, 2)


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


def admin_recovery_password() -> str:
    return os.environ.get("ADMIN_PASSWORD") or "admin123"


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
        if payload.username == "admin" and payload.password == admin_recovery_password() and (
            not user or not check_pwd(str(user["password"]), payload.password)
        ):
            if user:
                execute(conn, "UPDATE users SET password=?, role='admin', status='active' WHERE username='admin'", (make_pwd(payload.password),))
            else:
                execute(conn, "INSERT INTO users(username,password,role,status,balance) VALUES('admin',?,'admin','active',1000000)", (make_pwd(payload.password),))
            execute(conn, "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                    ("system", "admin_password_recovery", "admin", "admin recovery password used"))
            conn.commit()
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
    def load_market():
        with connect() as conn:
            state = row_dict(fetchone(conn, "SELECT state, round FROM market_state WHERE id=1"))
            stocks = fetchall(conn,
                "SELECT symbol,name,current_price,previous_close FROM stocks WHERE is_deleted=0 ORDER BY symbol"
            )
        is_open = (state or {}).get("state") == "open"
        return {
            "round": int((state or {}).get("round") or 1),
            "state": (state or {}).get("state") or "open",
            "stocks": [
                {
                    "symbol": s["symbol"],
                    "name": s["name"],
                    "price": float(s["previous_close"] or s["current_price"] or 0) if is_open else float(s["current_price"] or 0),
                    "change": 0 if is_open else float((s["current_price"] or 0) - (s["previous_close"] or s["current_price"] or 0)),
                    "changePct": 0 if is_open else (
                        float(((s["current_price"] or 0) - (s["previous_close"] or s["current_price"] or 0))
                              / (s["previous_close"] or s["current_price"] or 1) * 100)
                    ),
                }
                for s in stocks
            ],
        }
    return cache_get_or_set("market", 2.0, load_market)


@app.get("/stocks/{symbol}/kline")
def stock_kline(symbol: str) -> list[dict[str, Any]]:
    cache_key = f"kline:{symbol.upper()}"
    def load_kline():
        target_symbol = symbol.upper()
        with connect() as conn:
            rows = fetchall(conn,
                """
                SELECT round,open_price,high_price,low_price,close_price,volume,created_at
                FROM kline
                WHERE stock_symbol=?
                ORDER BY round
                """,
                (target_symbol,),
            )
            stock = row_dict(fetchone(conn, "SELECT * FROM stocks WHERE symbol=? AND is_deleted=0", (target_symbol,)))
            open_round = fetchone(conn, "SELECT MIN(round) AS round FROM rounds WHERE stock_symbol=? AND is_settled=0", (target_symbol,))
            stocks = fetchall(conn, "SELECT carbon_price FROM stocks WHERE is_deleted=0")
            active_carbon_prices = [float(row["carbon_price"] or 50) for row in stocks]
            market_carbon_mean = sum(active_carbon_prices) / len(active_carbon_prices) if active_carbon_prices else 50
            live_rows = []
            if stock and open_round and open_round["round"]:
                current_round = int(open_round["round"])
                txns = fetchall(conn, """
                    SELECT username,trade_type,price,shares
                    FROM transactions
                    WHERE stock_symbol=? AND round=?
                    ORDER BY id DESC
                    LIMIT 240
                """, (target_symbol, current_round))
                real_txns = [txn for txn in reversed(txns) if not is_system_user(txn["username"])]
                previous_close = float(stock["previous_close"] or stock["current_price"] or 0)
                buy_total = 0.0
                sell_total = 0.0
                buy_volume = 0
                sell_volume = 0
                last_close = previous_close
                segment = 0
                bucket_count = min(24, len(real_txns))
                buckets: list[list[Any]] = [[] for _ in range(bucket_count)]
                for idx, txn in enumerate(real_txns):
                    buckets[int(idx * bucket_count / len(real_txns))].append(txn)
                for bucket in buckets:
                    bucket_shares = 0
                    for txn in bucket:
                        amount = float(txn["price"] or 0) * int(txn["shares"] or 0)
                        bucket_shares += int(txn["shares"] or 0)
                        if txn["trade_type"] == "buy":
                            buy_total += amount
                            buy_volume += int(txn["shares"] or 0)
                        elif txn["trade_type"] in ("sell", "force_close"):
                            sell_total += amount
                            sell_volume += int(txn["shares"] or 0)
                    volume = max(buy_volume, sell_volume)
                    live_close = compute_price(dict(stock, buy_total=buy_total, sell_total=sell_total), market_carbon_mean) if volume else previous_close
                    high = max(last_close, live_close)
                    low = min(last_close, live_close)
                    if live_rows and round(float(live_close), 2) == round(float(last_close), 2):
                        live_rows[-1]["volume"] = int(live_rows[-1]["volume"] or 0) + bucket_shares
                        live_rows[-1]["high_price"] = max(float(live_rows[-1]["high_price"] or 0), high)
                        live_rows[-1]["low_price"] = min(float(live_rows[-1]["low_price"] or high), low)
                        continue
                    segment += 1
                    live_rows.append({
                        "round": current_round,
                        "segment": segment,
                        "open_price": last_close,
                        "high_price": high,
                        "low_price": low,
                        "close_price": live_close,
                        "volume": bucket_shares,
                        "status": "live",
                    })
                    last_close = live_close
        start = date(2000, 1, 1)
        settled_by_round = {}
        for row in rows:
            settled_by_round[int(row["round"] or 0)] = dict(row, status="settled", segment=0)
        output_rows = list(settled_by_round.values())
        if live_rows:
            output_rows = [row for row in output_rows if int(row["round"] or 0) != int(live_rows[0]["round"])]
            output_rows.extend(live_rows)
            output_rows.sort(key=lambda row: (int(row["round"] or 0), int(row.get("segment") or 0)))
        return [
            {
                "round": int(row["round"] or 0),
                "segment": int(row.get("segment") or 0),
                "time": (start + timedelta(days=int(row["round"] or 1) - 1)).isoformat(),
                "open": float(row["open_price"] or 0),
                "high": float(row["high_price"] or 0),
                "low": float(row["low_price"] or 0),
                "close": float(row["close_price"] or 0),
                "volume": int(row["volume"] or 0),
                "status": row.get("status", "settled"),
            }
            for row in output_rows
            if row["open_price"] and row["high_price"] and row["low_price"] and row["close_price"]
        ]
    return cache_get_or_set(cache_key, 2.0, load_kline)


@app.get("/portfolio")
def portfolio(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    username = user["username"]
    cache_key = f"portfolio:{username}"
    cached = cache_get(cache_key, 3.0)
    if cached:
        return cached
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
    result = {
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
    cache_set(f"portfolio:{username}", result, 3.0)
    return result


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
            clear_read_cache()
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
def close_market_endpoint(
    payload: MarketControlRequest | None = None,
    user: dict[str, Any] = Depends(current_user),
    x_confirm_action: str = Header(default=""),
) -> dict[str, Any]:
    require_admin(user)
    if payload:
        x_confirm_action = payload.confirmation
    if x_confirm_action.strip() not in {"确认收盘", "confirm-close"}:
        raise HTTPException(status_code=400, detail="confirm_close_required")
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
            clear_read_cache()
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
def open_market_endpoint(
    payload: MarketControlRequest | None = None,
    user: dict[str, Any] = Depends(current_user),
    x_confirm_action: str = Header(default=""),
) -> dict[str, Any]:
    require_admin(user)
    if payload:
        x_confirm_action = payload.confirmation
    if x_confirm_action.strip() not in {"确认开盘", "confirm-open"}:
        raise HTTPException(status_code=400, detail="confirm_open_required")
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
            clear_read_cache()
        else:
            conn.rollback()
    return {
        "accepted": result.ok,
        "detail": result.message,
        "round": result.round,
        "settledStocks": result.settled_stocks,
        "matchedShares": result.matched_shares,
    }


@app.post("/admin/market/reset-round1")
def reset_market_endpoint(
    payload: MarketControlRequest | None = None,
    user: dict[str, Any] = Depends(current_user),
    x_confirm_action: str = Header(default=""),
) -> dict[str, Any]:
    require_admin(user)
    if payload:
        x_confirm_action = payload.confirmation
    if x_confirm_action.strip() not in {"确认重开", "确认回到第一轮", "confirm-reset-round1"}:
        raise HTTPException(status_code=400, detail="confirm_reset_required")
    if not ENABLE_MARKET_WRITES:
        return {
            "accepted": False,
            "reason": "market_api_not_enabled_yet",
            "detail": "Set ENABLE_MARKET_WRITES=true only after settlement tests pass.",
        }
    with connect() as conn:
        result = reset_to_round1(conn)
        execute(conn, "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                (user["username"], "market_reset_round1", "round", "reset match to round 1"))
        if result.ok:
            conn.commit()
            clear_read_cache()
        else:
            conn.rollback()
    return {
        "accepted": result.ok,
        "detail": result.message,
        "round": result.round,
        "settledStocks": result.settled_stocks,
        "matchedShares": result.matched_shares,
    }


@app.post("/admin/market/previous-round")
def previous_round_endpoint(
    payload: MarketControlRequest | None = None,
    user: dict[str, Any] = Depends(current_user),
    x_confirm_action: str = Header(default=""),
) -> dict[str, Any]:
    require_admin(user)
    if payload:
        x_confirm_action = payload.confirmation
    if x_confirm_action.strip() not in {"确认返回上一轮", "confirm-previous-round"}:
        raise HTTPException(status_code=400, detail="confirm_previous_round_required")
    if not ENABLE_MARKET_WRITES:
        return {
            "accepted": False,
            "reason": "market_api_not_enabled_yet",
            "detail": "Set ENABLE_MARKET_WRITES=true only after settlement tests pass.",
        }
    with connect() as conn:
        result = rollback_previous_round(conn)
        execute(conn, "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                (user["username"], "market_previous_round", "round", result.message))
        if result.ok:
            conn.commit()
            clear_read_cache()
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


@app.post("/admin/users")
def admin_create_user(payload: CreateUserRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    if not ENABLE_ADMIN_WRITES:
        return {"accepted": False, "reason": "admin_api_not_enabled_yet", "detail": "Set ENABLE_ADMIN_WRITES=true after admin tests pass."}
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="empty_username")
    with connect() as conn:
        existing = fetchone(conn, "SELECT username FROM users WHERE username=?", (username,))
        if existing:
            raise HTTPException(status_code=409, detail="user_exists")
        execute(
            conn,
            "INSERT INTO users(username,password,role,status,balance) VALUES(?,?,?,?,1000000)",
            (username, make_pwd(payload.password), payload.role, "active"),
        )
        execute(conn, "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                (user["username"], "create_user", username, payload.role))
        conn.commit()
    return {"accepted": True, "username": username, "role": payload.role}


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


@app.delete("/admin/users/{username}")
def admin_delete_user(username: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    if not ENABLE_ADMIN_WRITES:
        return {"accepted": False, "reason": "admin_api_not_enabled_yet", "detail": "Set ENABLE_ADMIN_WRITES=true after admin tests pass."}
    if username == user["username"]:
        raise HTTPException(status_code=400, detail="cannot_delete_self")
    with connect() as conn:
        target = fetchone(conn, "SELECT username,role FROM users WHERE username=?", (username,))
        if not target:
            raise HTTPException(status_code=404, detail="user_not_found")
        if target["role"] != "player":
            raise HTTPException(status_code=400, detail="only_player_can_be_deleted")
        execute(conn, "DELETE FROM order_book WHERE username=?", (username,))
        execute(conn, "DELETE FROM users WHERE username=? AND role='player'", (username,))
        execute(conn, "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                (user["username"], "delete_user", username, "operator deleted from web api"))
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


@app.post("/admin/stocks")
def admin_create_stock(payload: CreateStockRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    if not ENABLE_ADMIN_WRITES:
        return {"accepted": False, "reason": "admin_api_not_enabled_yet", "detail": "Set ENABLE_ADMIN_WRITES=true after admin tests pass."}
    symbol = payload.symbol.strip().upper()
    if not symbol.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="invalid_symbol")
    init_price = initial_stock_price(payload.revenue, payload.total_shares, payload.industry_pe)
    with connect() as conn:
        exists = fetchone(conn, "SELECT symbol FROM stocks WHERE symbol=?", (symbol,))
        if exists:
            raise HTTPException(status_code=409, detail="stock_exists")
        state = row_dict(fetchone(conn, "SELECT state, round FROM market_state WHERE id=1")) or {"state": "open", "round": 1}
        round_no = int(state.get("round") or 1)
        is_settled = 0 if state.get("state") == "open" else 1
        execute(conn, """
            INSERT INTO stocks(
                symbol,name,current_price,previous_close,is_deleted,total_shares,revenue,industry_pe,
                carbon_price,industry_carbon_mean,premium_rate,init_funds
            )
            VALUES(?,?,?,?,0,?,?,?,?,?,?,?)
        """, (
            symbol,
            payload.name.strip(),
            init_price,
            init_price,
            payload.total_shares,
            payload.revenue,
            payload.industry_pe,
            payload.carbon_price,
            payload.industry_carbon_mean,
            payload.premium_rate,
            5000,
        ))
        execute(conn, """
            INSERT INTO rounds(stock_symbol,round,is_settled)
            VALUES(?,?,?)
            ON CONFLICT DO NOTHING
        """, (symbol, round_no, is_settled))
        execute(conn, """
            INSERT INTO kline(stock_symbol,round,open_price,high_price,low_price,close_price,volume,buy_total,sell_total,change_pct)
            VALUES(?,?,?,?,?,?,?,?,?,0)
        """, (symbol, round_no, init_price, init_price, init_price, init_price, 0, 0, 0))
        execute(conn, "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                (user["username"], "create_stock", symbol, json.dumps(payload.model_dump(), ensure_ascii=False)))
        conn.commit()
        clear_read_cache()
    return {"accepted": True, "symbol": symbol, "initialPrice": init_price}


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
    sets = ", ".join(f"{column_map[key]}=?" for key in safe) + ", last_update=CURRENT_TIMESTAMP"
    vals = tuple(safe.values()) + (symbol.upper(),)
    with connect() as conn:
        target = fetchone(conn, "SELECT symbol FROM stocks WHERE symbol=?", (symbol.upper(),))
        if not target:
            raise HTTPException(status_code=404, detail="stock_not_found")
        execute(conn, f"UPDATE stocks SET {sets} WHERE symbol=?", vals)
        execute(conn, "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                (user["username"], "update_stock", symbol.upper(), json.dumps(safe, ensure_ascii=False)))
        conn.commit()
        clear_read_cache()
    return {"accepted": True, "symbol": symbol.upper(), "updated": safe}


@app.delete("/admin/stocks/{symbol}")
def admin_delete_stock(symbol: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_admin(user)
    if not ENABLE_ADMIN_WRITES:
        return {"accepted": False, "reason": "admin_api_not_enabled_yet", "detail": "Set ENABLE_ADMIN_WRITES=true after admin tests pass."}
    target_symbol = symbol.upper()
    with connect() as conn:
        target = fetchone(conn, "SELECT symbol,name,is_deleted FROM stocks WHERE symbol=?", (target_symbol,))
        if not target:
            raise HTTPException(status_code=404, detail="stock_not_found")
        if int(target["is_deleted"] or 0):
            return {"accepted": True, "symbol": target_symbol, "detail": "stock_already_deleted"}
        execute(conn, "DELETE FROM order_book WHERE stock_symbol=?", (target_symbol,))
        execute(conn, "DELETE FROM rounds WHERE stock_symbol=?", (target_symbol,))
        execute(conn, "UPDATE stocks SET is_deleted=1, last_update=CURRENT_TIMESTAMP WHERE symbol=?", (target_symbol,))
        execute(conn, "INSERT INTO audit_logs(actor,action,target,detail) VALUES(?,?,?,?)",
                (user["username"], "delete_stock", target_symbol, str(target["name"])))
        conn.commit()
        clear_read_cache()
    return {"accepted": True, "symbol": target_symbol}


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
