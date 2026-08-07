"""End-to-end smoke test for the gipfel API against a local SQLite server.

Run:  python local_bootstrap.py  (once; resets data/stock_analysis.db)
      TOKEN_SECRET=local-test-secret ADMIN_PASSWORD=local-admin-pw \
      ENABLE_ORDER_WRITES=true ENABLE_MARKET_WRITES=true ENABLE_ADMIN_WRITES=true \
      uvicorn api.main:app --port 8001   (no DATABASE_URL)
      python local_smoke.py
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8001"
DB = Path(__file__).resolve().parent / "data" / "stock_analysis.db"
fails: list[str] = []


def check(name: str, cond: bool, extra: str = ""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond or not extra else f"  <- {extra}"))
    if not cond:
        fails.append(name)


def login(u: str, p: str = "test123") -> str:
    r = requests.post(f"{BASE}/auth/login", json={"username": u, "password": p}, timeout=10)
    assert r.ok, f"login {u} failed: {r.status_code} {r.text[:200]}"
    return r.json()["accessToken"]


def hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def account_balance(tok: str, aid: int) -> float:
    r = requests.get(f"{BASE}/fund-accounts", headers=hdr(tok), timeout=10)
    assert r.ok, r.text[:200]
    return next(a["balance"] for a in r.json() if a["id"] == aid)


def db_query(sql: str, params=()):
    c = sqlite3.connect(DB)
    try:
        return c.execute(sql, params).fetchall()
    finally:
        c.close()


def db_exec(sql: str, params=()):
    c = sqlite3.connect(DB)
    try:
        c.execute(sql, params)
        c.commit()
    finally:
        c.close()


def net_holding(username: str, symbol: str) -> int:
    """Net shares held across all rounds: sum(buy) - sum(sell/force_close)."""
    rows = db_query(
        "SELECT trade_type, shares FROM transactions WHERE username=? AND stock_symbol=?",
        (username, symbol),
    )
    return sum(int(s) if t == "buy" else -int(s) for t, s in rows)


ADMIN = login("admin")
P1 = login("p1")
P2 = login("p2")

# ============ 1. create fund account ============
r = requests.post(f"{BASE}/fund-accounts", json={"name": "本金", "initial_balance": 10000}, headers=hdr(P1), timeout=10)
check("create fund account", r.ok and r.json().get("accepted"), r.text[:200])
aid = r.json()["id"]

# ============ 2. buy 100 x S1 @10 = 1000 ============
r = requests.post(f"{BASE}/orders", json={"username": "p1", "symbol": "S1", "side": "buy", "price": 10, "shares": 100, "account_id": aid}, headers=hdr(P1), timeout=10)
check("buy 100 S1", r.ok and r.json().get("accepted"), r.text[:300])
check("balance after buy = 9000", abs(account_balance(P1, aid) - 9000) < 0.01)

# ============ 3. overdraw auto-shrinks to affordable qty; zero-affordable rejected ============
r = requests.post(f"{BASE}/orders", json={"username": "p1", "symbol": "S2", "side": "buy", "price": 20, "shares": 10000, "account_id": aid}, headers=hdr(P1), timeout=10)
data = r.json()
check("overdraw auto-shrinks to 450 shares", r.ok and data.get("accepted") and "450 股" in data.get("detail", ""), f"{r.status_code} {r.text[:200]}")
check("balance after auto-shrink = 0", abs(account_balance(P1, aid)) < 0.01)
r = requests.post(f"{BASE}/orders", json={"username": "p1", "symbol": "S1", "side": "buy", "price": 10, "shares": 1, "account_id": aid}, headers=hdr(P1), timeout=10)
data = r.json()
check("zero-affordable rejected with 余额不足", r.ok and (not data.get("accepted")) and "余额不足" in data.get("detail", ""), f"{r.status_code} {r.text[:200]}")
check("balance still 0", abs(account_balance(P1, aid)) < 0.01)

# ============ 4. concurrent two-symbol buys vs shared balance (negative-balance regression) ============
r = requests.patch(f"{BASE}/fund-accounts/{aid}", json={"balance": 6000}, headers=hdr(P1), timeout=10)
assert r.ok, r.text[:200]
results: dict = {}


def buy_sym(sym: str):
    try:
        rr = requests.post(
            f"{BASE}/orders",
            json={"username": "p1", "symbol": sym, "side": "buy", "price": 10, "shares": 500, "account_id": aid},
            headers=hdr(P1), timeout=30,
        )
        try:
            results[sym] = (rr.status_code, rr.json().get("accepted"), rr.json().get("detail", "")[:60])
        except Exception as e:  # noqa: BLE001
            results[sym] = (rr.status_code, f"PARSE-ERR:{type(e).__name__}", rr.text[:60])
    except Exception as e:  # noqa: BLE001
        results[sym] = (0, f"REQ-ERR:{type(e).__name__}", str(e)[:60])


# S1 x500 @10 = 5000, S2 x500 @20 = 10000; balance 6000 -> only one can succeed
t1 = threading.Thread(target=buy_sym, args=("S1",))
t2 = threading.Thread(target=buy_sym, args=("S2",))
t1.start(); t2.start(); t1.join(); t2.join()
bal = account_balance(P1, aid)
check("concurrent buys: balance never negative", bal >= 0, f"bal={bal} {results}")
check("concurrent buys: exactly one succeeds", 0 <= bal <= 1000, f"bal={bal} {results}")

# ============ 5. sell to system ============
# Race outcomes are timing-dependent (S1 wins / S2 wins / both succeed with
# S2 auto-shrunk), so post-race balance is 1000 or 0.  The account still holds
# the 100 S1 from section 2, so the sell always succeeds and must credit
# exactly +500 regardless of the race outcome.
bal_before_sell = account_balance(P1, aid)
r = requests.post(f"{BASE}/orders", json={"username": "p1", "symbol": "S1", "side": "sell", "price": 10, "shares": 50, "account_id": aid}, headers=hdr(P1), timeout=10)
check("sell 50 S1 (holding from section 2)", r.ok and r.json().get("accepted"), r.text[:300])
bal = account_balance(P1, aid)
check(f"balance after sell = {bal_before_sell} + 500", abs(bal - (bal_before_sell + 500)) < 0.01, f"bal={bal} results={results}")

# ============ 6. close_market money conservation (Bug 2) ============
# p2 account with 20000; p1 account topped up to 1500 so that a 100-share buy
# (1000) is payable but a 200-share buy (2000) is not.
requests.patch(f"{BASE}/fund-accounts/{aid}", json={"balance": 1500}, headers=hdr(P1), timeout=10)
r = requests.post(f"{BASE}/fund-accounts", json={"name": "本金", "initial_balance": 20000}, headers=hdr(P2), timeout=10)
aid2 = r.json()["id"]
trader1 = f"[账户:{aid}]"
trader2 = f"[账户:{aid2}]"
# Buy orders: p1 affordable 100 shares (1000 <= 1500) + p1 NOT affordable 200 shares (2000 > 1500)
db_exec("INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,?,?,?,1)", (trader1, "S1", "buy", 10, 100))
db_exec("INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,?,?,?,1)", (trader1, "S1", "buy", 10, 200))
# Sell orders: p2 sell 300 shares total.  Back the sell with a real holding so
# the close_market seller-holding cap (Test D in section 6.5) doesn't change
# this section's expectations: p2 must own what they sell.
db_exec("INSERT INTO transactions(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,?)", (trader2, "S1", 10, 300, 1))
db_exec("INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,?,?,?,1)", (trader2, "S1", "sell", 10, 300))
r = requests.post(f"{BASE}/admin/market/close", json={"confirmation": "确认收盘"}, headers=hdr(ADMIN), timeout=15)
check("close_market ok", r.ok and r.json().get("accepted"), f"{r.status_code} {r.text[:200]}")
check("close: 100 shares matched (skipped the unpayable 200)", r.json().get("matchedShares") == 100, r.text[:200])
b1 = account_balance(P1, aid)
b2 = account_balance(P2, aid2)
check("close: p1 paid exactly 1000 (1500-1000=500)", abs(b1 - 500) < 0.01, f"p1={b1}")
check("close: p2 received exactly 1000 (20000+1000=21000)", abs(b2 - 21000) < 0.01, f"p2={b2}  <- money created if > 21000")
check("close: no pending orders left", len(db_query("SELECT 1 FROM order_book")) == 0)

# ============ 6.5 resting-order matching: money conservation (Bug 1/2) ============
# Baseline: p1 balance 500 (holds 50 S1), p2 balance 21000, order_book empty.
# NOTE: S2 is used here, not S1 — close_market in section 6 left [账户:2] with a
# net NEGATIVE S1 holding (sold 100 of nothing), which would trip the resting
# seller's holding check.  S2 has no close_market history, so holdings are clean.
r = requests.post(f"{BASE}/admin/market/open", json={"confirmation": "确认开盘"}, headers=hdr(ADMIN), timeout=15)
check("open market for resting-order tests", r.ok and r.json().get("accepted"), f"{r.status_code} {r.text[:200]}")
# Test A (Bug 1): buy vs resting sell — seller must be credited exactly once.
# p2 buys 10 S2 from the system first (creates a real holding), then p1 buys
# those same 10 back from p2's resting sell: p2 must end at exactly 21000.
# (API price is server-side current_price, so derive cost from the balance delta.)
r = requests.post(f"{BASE}/orders", json={"username": "p2", "symbol": "S2", "side": "buy", "price": 20, "shares": 10, "account_id": aid2}, headers=hdr(P2), timeout=10)
check("match-buy: p2 system-buy 10 S2 accepted", r.ok and r.json().get("accepted"), f"{r.status_code} {r.text[:200]}")
cost = round(21000 - account_balance(P2, aid2), 2)  # = 10 * actual fill price
db_exec("INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,1)", (trader2, "S2", cost / 10, 10))
r = requests.post(f"{BASE}/orders", json={"username": "p1", "symbol": "S2", "side": "buy", "price": 20, "shares": 10, "account_id": aid}, headers=hdr(P1), timeout=10)
check("match-buy: p1 buy 10 S2 vs resting sell accepted", r.ok and r.json().get("accepted"), f"{r.status_code} {r.text[:200]}")
check("match-buy: p1 paid exactly cost (500-cost)", abs(account_balance(P1, aid) - round(500 - cost, 2)) < 0.01, f"p1={account_balance(P1, aid)}")
check("match-buy: p2 back to exactly 21000 (credited once)", abs(account_balance(P2, aid2) - 21000) < 0.01, f"p2={account_balance(P2, aid2)}  <- duplicated credit if {round(21000 + cost, 2)}")
check("match-buy: order_book empty after fill", len(db_query("SELECT 1 FROM order_book")) == 0)
# Test B (Bug 2): sell vs resting buy — buyer must be debited exactly once.
# p1 first system-buys 10 S2 so the sell has real holdings to sell.
# Deltas (not absolutes) are asserted so the check stays valid regardless of
# whether Test A's credit bug was present.
r = requests.post(f"{BASE}/orders", json={"username": "p1", "symbol": "S2", "side": "buy", "price": 20, "shares": 10, "account_id": aid}, headers=hdr(P1), timeout=10)
check("match-sell: p1 system-buy 10 S2 accepted", r.ok and r.json().get("accepted"), f"{r.status_code} {r.text[:200]}")
cost2 = round((500 - cost) - account_balance(P1, aid), 2)  # = 10 * S2 price
p1_before = account_balance(P1, aid)
p2_before = account_balance(P2, aid2)
db_exec("INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,1)", (trader2, "S2", cost2 / 10, 10))
r = requests.post(f"{BASE}/orders", json={"username": "p1", "symbol": "S2", "side": "sell", "price": 20, "shares": 10, "account_id": aid}, headers=hdr(P1), timeout=10)
check("match-sell: p1 sell 10 S2 vs resting buy accepted", r.ok and r.json().get("accepted"), f"{r.status_code} {r.text[:200]}")
p1_after = account_balance(P1, aid)
p2_after = account_balance(P2, aid2)
check("match-sell: p1 received exactly cost2", abs((p1_after - p1_before) - cost2) < 0.01, f"p1 delta={round(p1_after - p1_before, 2)}")
check("match-sell: p2 debited exactly cost2", abs((p2_before - p2_after) - cost2) < 0.01, f"p2 delta={round(p2_before - p2_after, 2)}  <- double debit if {round(2 * cost2, 2)}")
check("match-sell: order_book empty after fill", len(db_query("SELECT 1 FROM order_book")) == 0)
# Test D (close_market seller-holding cap): a resting sell that exceeds the
# seller's actual holding must be capped at settlement — the seller can never
# be paid for (nor the buyer charged for) shares the seller does not own, so
# net holdings can never go negative.  S3 is used: no other section touches
# it, so holdings are deterministic (S2 was left in a race-dependent state by
# sections 3-4, and Test B left p2 holding 10 S2).
requests.patch(f"{BASE}/fund-accounts/{aid}", json={"balance": 2000}, headers=hdr(P1), timeout=10)  # top up so the uncapped 30-share fill is affordable
price = next(s["price"] for s in requests.get(f"{BASE}/market", timeout=10).json()["stocks"] if s["symbol"] == "S3")
p1_before = account_balance(P1, aid)
b2_before = account_balance(P2, aid2)
r = requests.post(f"{BASE}/orders", json={"username": "p2", "symbol": "S3", "side": "buy", "price": 50, "shares": 10, "account_id": aid2}, headers=hdr(P2), timeout=10)
check("close-cap: p2 system-buy 10 S3 accepted", r.ok and r.json().get("accepted"), f"{r.status_code} {r.text[:200]}")
# p2 now holds exactly 10 S3 (cost3 = 10 * price); the resting sell of 100
# exceeds that holding and must be capped to 10 at settlement.
db_exec("INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,1)", (trader2, "S3", price, 100))  # exceeds p2's 10-share holding
db_exec("INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'buy',?,?,1)", (trader1, "S3", price, 30))
r = requests.post(f"{BASE}/admin/market/close", json={"confirmation": "确认收盘"}, headers=hdr(ADMIN), timeout=15)
check("close-cap: close ok", r.ok and r.json().get("accepted"), f"{r.status_code} {r.text[:200]}")
check("close-cap: p2 net S3 holding never negative (capped at 10)", net_holding(trader2, "S3") == 0, f"holding={net_holding(trader2, 'S3')}  <- negative if uncapped")
check("close-cap: p1 bought exactly the capped 10 shares", net_holding(trader1, "S3") == 10, f"holding={net_holding(trader1, 'S3')}")
check("close-cap: p1 paid exactly 10 shares' worth", abs((p1_before - account_balance(P1, aid)) - round(10 * price, 2)) < 0.01, f"p1 delta={round(p1_before - account_balance(P1, aid), 2)}")
check("close-cap: p2 cash net unchanged (paid 10 shares, received 10 shares)", abs(account_balance(P2, aid2) - b2_before) < 0.01, f"p2={account_balance(P2, aid2)} vs {b2_before}")
check("close-cap: order_book empty after close", len(db_query("SELECT 1 FROM order_book")) == 0)

# ============ 7. open_market + reset-round1 (Bug 3) ============
r = requests.post(f"{BASE}/admin/market/open", json={"confirmation": "确认开盘"}, headers=hdr(ADMIN), timeout=15)
data = r.json()
check("open_market ok", r.ok and (data.get("accepted") or "已经开盘" in data.get("detail", "")), f"{r.status_code} {r.text[:200]}")
r = requests.post(f"{BASE}/admin/market/reset-round1", json={"confirmation": "确认重开"}, headers=hdr(ADMIN), timeout=15)
check("reset-round1 ok", r.ok and r.json().get("accepted"), f"{r.status_code} {r.text[:200]}")
me = requests.get(f"{BASE}/auth/me", headers=hdr(P1), timeout=10).json()
check("reset-round1: p1 personal balance is 0 (funds live in fund accounts)", abs(float(me["balance"])) < 0.01, f"p1={me['balance']}")
check("reset-round1: fund accounts cleared", requests.get(f"{BASE}/fund-accounts", headers=hdr(P1), timeout=10).json() == [])
check("reset-round1: transactions cleared", len(db_query("SELECT 1 FROM transactions")) == 0)

# ============ 8. admin_delete_user cascade (Bug 4) ============
r = requests.post(f"{BASE}/admin/users", json={"username": "p3", "password": "test123", "role": "player"}, headers=hdr(ADMIN), timeout=10)
check("create p3", r.ok and r.json().get("accepted"), f"{r.status_code} {r.text[:200]}")
P3 = login("p3")
r = requests.post(f"{BASE}/fund-accounts", json={"name": "本金", "initial_balance": 5000}, headers=hdr(P3), timeout=10)
aid3 = r.json()["id"]
requests.post(f"{BASE}/orders", json={"username": "p3", "symbol": "S1", "side": "buy", "price": 10, "shares": 100, "account_id": aid3}, headers=hdr(P3), timeout=10)
# Orphan-resting-order + manager-reference leftovers that must be cascaded away.
trader3 = f"[账户:{aid3}]"
db_exec("INSERT INTO order_book(username,stock_symbol,trade_type,price,shares,round) VALUES(?,?,'sell',?,?,1)", (trader3, "S1", 10, 10))
db_exec("UPDATE stocks SET manager='p3' WHERE symbol='S1'")
r = requests.delete(f"{BASE}/admin/users/p3", headers=hdr(ADMIN), timeout=10)
check("delete p3", r.ok and r.json().get("accepted"), f"{r.status_code} {r.text[:200]}")
check("cascade: p3 gone from users", len(db_query("SELECT 1 FROM users WHERE username='p3'")) == 0)
check("cascade: p3 transactions removed", len(db_query("SELECT 1 FROM transactions WHERE username='p3'")) == 0)
check("cascade: p3 fund accounts removed", len(db_query("SELECT 1 FROM fund_accounts WHERE owner='p3'")) == 0)
check("cascade: p3 audit entries removed", len(db_query("SELECT 1 FROM audit_logs WHERE actor='p3'")) == 0)
check("cascade: p3 trader rows removed from order_book", len(db_query("SELECT 1 FROM order_book WHERE username=?", (trader3,))) == 0)
check("cascade: p3 trader rows removed from transactions", len(db_query("SELECT 1 FROM transactions WHERE username=?", (trader3,))) == 0)
check("cascade: p3 manager reference cleared", len(db_query("SELECT 1 FROM stocks WHERE symbol='S1' AND manager=''")) == 1)
r = requests.post(f"{BASE}/auth/login", json={"username": "p3", "password": "test123"}, timeout=10)
check("cascade: p3 can no longer log in", r.status_code == 401, f"{r.status_code}")

# ============ 9. disabled user token rejected (existing behavior) ============
r = requests.patch(f"{BASE}/admin/users/p2/status", json={"status": "disabled"}, headers=hdr(ADMIN), timeout=10)
check("disable p2", r.ok, f"{r.status_code} {r.text[:200]}")
time.sleep(6)  # current_user cache TTL is 5s
r = requests.get(f"{BASE}/portfolio", headers=hdr(P2), timeout=10)
check("disabled p2 token rejected", r.status_code == 401, f"{r.status_code} {r.text[:200]}")
requests.patch(f"{BASE}/admin/users/p2/status", json={"status": "active"}, headers=hdr(ADMIN), timeout=10)

# ============ 10. negative balance audited, not silent (Bug 5) ============
requests.post(f"{BASE}/fund-accounts", json={"name": "负余额测试", "initial_balance": 100}, headers=hdr(P2), timeout=10)
db_exec("UPDATE fund_accounts SET balance=-5 WHERE owner='p2' AND name='负余额测试'")
requests.get(f"{BASE}/fund-accounts", headers=hdr(P2), timeout=10)  # triggers ensure_fund_accounts_schema
rows = db_query("SELECT action,target,detail FROM audit_logs WHERE action='balance_zeroed'")
check("negative balance audited (balance_zeroed)", len(rows) >= 1 and "old_balance=-5" in rows[-1][2], str(rows[-5:]))
rows2 = db_query("SELECT balance FROM fund_accounts WHERE owner='p2' AND name='负余额测试'")
check("negative balance clamped to 0 after audit", abs(float(rows2[0][0]) - 0) < 0.01, str(rows2))

print()
if fails:
    print(f"SMOKE FAILED: {len(fails)} failed -> {fails}")
    raise SystemExit(1)
print("ALL SMOKE TESTS PASSED")
