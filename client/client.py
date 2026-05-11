#!/usr/bin/env python3
"""
One-shot distributed inference client.
Fires all queries simultaneously, collects responses, prints summary.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone

import httpx

COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "http://control:80")
NUM_REQUESTS    = int(os.environ.get("NUM_REQUESTS", "20"))
TIMEOUT         = float(os.environ.get("TIMEOUT", "180"))

QUERIES = [
    "What is distributed computing?",
    "Explain fault tolerance in distributed systems.",
    "What is the CAP theorem?",
    "How does consensus work in distributed systems?",
    "What is load balancing?",
    "Explain horizontal vs vertical scaling.",
    "What is a message queue?",
    "How do distributed databases handle replication?",
    "What is eventual consistency?",
    "Explain sharding in distributed databases.",
]


async def send(client: httpx.AsyncClient, idx: int, query: str) -> dict:
    start = time.monotonic()
    try:
        r = await client.post(f"{COORDINATOR_URL}/query", json={"query": query}, timeout=TIMEOUT)
        elapsed = (time.monotonic() - start) * 1000
        if r.status_code == 200:
            d = r.json()
            print(f"  [{idx:3d}] OK     worker={d.get('worker_id','?')[:8]}  "
                  f"lat={elapsed/1000:.2f}s  retries={d.get('retry_count',0)}")
            print(f"         Q: {query[:60]}")
            print(f"         A: {d.get('answer','')[:120]}")
            print()
            return {"ok": True, "worker_id": d.get("worker_id", "?"),
                    "latency_ms": elapsed, "retry_count": d.get("retry_count", 0)}
        print(f"  [{idx:3d}] ERR    status={r.status_code}  lat={elapsed/1000:.2f}s")
        return {"ok": False, "latency_ms": elapsed, "status": r.status_code}
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        print(f"  [{idx:3d}] FAIL   {e}")
        return {"ok": False, "latency_ms": elapsed, "error": str(e)}


async def main() -> None:
    print(f"\n{'='*60}")
    print(f"  Distributed RAG Inference Client")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Coordinator : {COORDINATOR_URL}")
    print(f"  Requests    : {NUM_REQUESTS} (fired simultaneously)")
    print(f"{'='*60}\n")

    async with httpx.AsyncClient() as client:
        # wait for coordinator to be ready
        for _ in range(10):
            try:
                r = await client.get(f"{COORDINATOR_URL}/health", timeout=5)
                health = r.json()
                if health.get("healthy_workers", 0) > 0:
                    print(f"  Cluster ready: {health['healthy_workers']} healthy workers\n")
                    break
                print(f"  Waiting for workers... (healthy={health.get('healthy_workers',0)})")
            except Exception:
                print("  Waiting for coordinator...")
            await asyncio.sleep(3)

        start = time.monotonic()
        results = await asyncio.gather(*[
            send(client, i + 1, QUERIES[i % len(QUERIES)])
            for i in range(NUM_REQUESTS)
        ])
        wall = time.monotonic() - start

    ok   = [r for r in results if r.get("ok")]
    fail = [r for r in results if not r.get("ok")]
    lats = sorted(r["latency_ms"] for r in ok)
    n    = len(lats)

    worker_counts: dict[str, int] = defaultdict(int)
    for r in ok:
        worker_counts[r["worker_id"][:8]] += 1

    print(f"\n{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    print(f"  Total requests : {NUM_REQUESTS}")
    print(f"  Successful     : {len(ok)}")
    print(f"  Failed         : {len(fail)}")
    print(f"  Success rate   : {len(ok)/NUM_REQUESTS*100:.1f}%")
    print(f"  Throughput     : {NUM_REQUESTS/wall:.2f} req/s")
    print(f"  Wall time      : {wall:.1f}s")
    if n:
        print(f"\n  Latency")
        print(f"    mean  : {statistics.mean(lats)/1000:.2f}s")
        print(f"    p50   : {statistics.median(lats)/1000:.2f}s")
        print(f"    p95   : {lats[int(n*0.95)]/1000:.2f}s")
        print(f"    p99   : {lats[min(int(n*0.99),n-1)]/1000:.2f}s")
        print(f"    min   : {lats[0]/1000:.2f}s")
        print(f"    max   : {lats[-1]/1000:.2f}s")
    if worker_counts:
        print(f"\n  Worker distribution")
        for wid, count in sorted(worker_counts.items(), key=lambda x: -x[1]):
            print(f"    {wid} : {count}")
    if fail:
        print(f"\n  Failures")
        by_err: dict[str, int] = {}
        for r in fail:
            key = f"HTTP {r.get('status','?')}" if "status" in r else r.get("error","unknown")[:60]
            by_err[key] = by_err.get(key, 0) + 1
        for err, count in sorted(by_err.items(), key=lambda x: -x[1]):
            print(f"    [{count}x] {err}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
