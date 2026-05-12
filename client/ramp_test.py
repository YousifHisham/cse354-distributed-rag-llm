#!/usr/bin/env python3
"""
Ramp test — gradually increases concurrency from 1 to MAX over DURATION seconds.
Shows how the system behaves under increasing load.

Usage:
  python3 ramp_test.py
  python3 ramp_test.py --max-users 50 --duration 60 --steps 5
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time
from collections import defaultdict

import httpx

COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "http://control:80")

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


async def send(client: httpx.AsyncClient, idx: int, query: str, step: int) -> dict:
    start = time.monotonic()
    try:
        r = await client.post(f"{COORDINATOR_URL}/query", json={"query": query}, timeout=300)
        elapsed = (time.monotonic() - start) * 1000
        if r.status_code == 200:
            d = r.json()
            return {"ok": True, "step": step, "worker_id": d.get("worker_id", "?"), "latency_ms": elapsed}
        return {"ok": False, "step": step, "latency_ms": elapsed, "status": r.status_code}
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return {"ok": False, "step": step, "latency_ms": elapsed, "error": str(e)}


async def run_step(client: httpx.AsyncClient, users: int, step: int) -> list[dict]:
    results = await asyncio.gather(*[
        send(client, i, QUERIES[i % len(QUERIES)], step)
        for i in range(users)
    ])
    return list(results)


async def main(max_users: int, steps: int, step_delay: float) -> None:
    step_sizes = [max(1, int(max_users * (i + 1) / steps)) for i in range(steps)]

    print(f"\n{'='*60}")
    print(f"  Ramp Test")
    print(f"  Steps     : {steps}  ({' → '.join(str(s) for s in step_sizes)} users)")
    print(f"  Step delay : {step_delay}s between steps")
    print(f"  Coordinator: {COORDINATOR_URL}")
    print(f"{'='*60}\n")

    async with httpx.AsyncClient() as client:
        r = await client.get(f"{COORDINATOR_URL}/health", timeout=10)
        health = r.json()
        print(f"  Cluster: {health['healthy_workers']} healthy workers\n")

        all_results: list[dict] = []

        for i, users in enumerate(step_sizes):
            print(f"  Step {i+1}/{steps} — {users} concurrent users")
            start = time.monotonic()
            results = await run_step(client, users, i + 1)
            wall = time.monotonic() - start
            all_results.extend(results)

            ok   = [r for r in results if r.get("ok")]
            lats = sorted(r["latency_ms"] for r in ok)
            n    = len(lats)
            throughput = users / wall if wall > 0 else 0

            print(f"    success={len(ok)}/{users}  "
                  f"throughput={throughput:.2f} req/s  "
                  f"p50={statistics.median(lats)/1000:.2f}s  "
                  f"p95={lats[int(n*0.95)]/1000:.2f}s" if n else
                  f"    success={len(ok)}/{users}  throughput={throughput:.2f} req/s")

            if i < len(step_sizes) - 1:
                await asyncio.sleep(step_delay)

    ok   = [r for r in all_results if r.get("ok")]
    lats = sorted(r["latency_ms"] for r in ok)
    n    = len(lats)
    total = len(all_results)

    worker_counts: dict[str, int] = defaultdict(int)
    for r in ok:
        worker_counts[r["worker_id"][:8]] += 1

    print(f"\n{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    print(f"  Total requests : {total}")
    print(f"  Successful     : {len(ok)}")
    print(f"  Success rate   : {len(ok)/total*100:.1f}%")
    if n:
        print(f"\n  Latency")
        print(f"    mean : {statistics.mean(lats)/1000:.2f}s")
        print(f"    p50  : {statistics.median(lats)/1000:.2f}s")
        print(f"    p95  : {lats[int(n*0.95)]/1000:.2f}s")
        print(f"    p99  : {lats[min(int(n*0.99),n-1)]/1000:.2f}s")
    if worker_counts:
        print(f"\n  Worker distribution")
        for wid, count in sorted(worker_counts.items(), key=lambda x: -x[1]):
            print(f"    {wid} : {count}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-users", type=int, default=50)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--step-delay", type=float, default=10.0)
    args = parser.parse_args()
    asyncio.run(main(args.max_users, args.steps, args.step_delay))
