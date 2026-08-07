"""Lightweight load test for the gipfel API.

Measures success rate and latency under configurable concurrency. Safe to
run against production: read-only endpoints by default. Enable --write to
exercise authenticated writes (login + market snapshot only).

Usage:
    python scripts/load_test.py --base https://gipfel-trading-api.onrender.com \
        --concurrency 20 --duration 30 --rate 10
    python scripts/load_test.py --base http://127.0.0.1:8001 --concurrency 5 --write
"""
import argparse
import os
import statistics
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

import requests

READ_PATHS = ["/health", "/market", "/stocks/JGONG/kline"]


@dataclass
class Result:
    ok: int = 0
    errors: Counter = field(default_factory=Counter)
    latencies: list[float] = field(default_factory=list)


def worker(base: str, paths: list[str], stop: threading.Event, result: Result, rate: float, session: requests.Session) -> None:
    i = 0
    while not stop.is_set():
        path = paths[i % len(paths)]
        i += 1
        start = time.perf_counter()
        try:
            r = session.get(f"{base}{path}", timeout=30)
            elapsed = (time.perf_counter() - start) * 1000
            if r.status_code == 200:
                result.ok += 1
                result.latencies.append(elapsed)
            else:
                result.errors[f"http_{r.status_code}"] += 1
        except Exception as exc:
            result.errors[type(exc).__name__] += 1
        if rate > 0:
            time.sleep(1.0 / rate)
        else:
            time.sleep(0)


def report(result: Result, duration: float) -> None:
    total = result.ok + sum(result.errors.values())
    print(f"\n=== Load test summary ({duration:.0f}s) ===")
    print(f"requests: {total}   ok: {result.ok} ({result.ok / max(total, 1) * 100:.1f}%)   errors: {total - result.ok}")
    if result.errors:
        for kind, n in result.errors.most_common():
            print(f"  error {kind}: {n}")
    if result.latencies:
        lat = sorted(result.latencies)
        n = len(lat)
        print(f"latency ms: p50={lat[n // 2]:.0f}  p90={lat[int(n * 0.9)]:.0f}  p99={lat[int(n * 0.99)]:.0f}  max={lat[-1]:.0f}  avg={statistics.mean(lat):.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://gipfel-trading-api.onrender.com")
    parser.add_argument("--concurrency", type=int, default=10, help="parallel workers")
    parser.add_argument("--duration", type=int, default=20, help="seconds to run")
    parser.add_argument("--rate", type=float, default=0, help="requests/sec per worker (0 = as fast as possible)")
    parser.add_argument("--write", action="store_true", help="include authenticated endpoints (needs ADMIN_PASSWORD)")
    args = parser.parse_args()

    paths = list(READ_PATHS)
    session = requests.Session()
    if args.write:
        password = os.environ.get("ADMIN_PASSWORD", "")
        if not password:
            parser.error("--write requires ADMIN_PASSWORD env var")
        r = session.post(f"{args.base}/auth/login", json={"username": "admin", "password": password}, timeout=20)
        if not r.ok:
            parser.error(f"login failed: {r.status_code} {r.text[:150]}")
        session.headers["Authorization"] = f"Bearer {r.json()['accessToken']}"
        paths.append("/auth/me")

    stop = threading.Event()
    result = Result()
    threads = [
        threading.Thread(target=worker, args=(args.base, paths, stop, result, args.rate, session))
        for _ in range(args.concurrency)
    ]
    start = time.monotonic()
    for t in threads:
        t.start()
    time.sleep(args.duration)
    stop.set()
    for t in threads:
        t.join()
    report(result, time.monotonic() - start)


if __name__ == "__main__":
    main()
