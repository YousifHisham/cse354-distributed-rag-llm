#!/usr/bin/env python3
"""
PDF load test — runs at concurrency levels 100, 500, 1000 as required by the project.
All requests at each level are fired simultaneously.

Usage:
  python3 pdf_load_test.py
  python3 pdf_load_test.py --levels 10 50 100
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


async def send(client: httpx.AsyncClient, idx: int, query: str) -> dict:
    start = time.monotonic()
    try:
        r = await client.post(f"{COORDINATOR_URL}/query", json={"query": query}, timeout=300)
        elapsed = (time.monotonic() - start) * 1000
        if r.status_code == 200:
            d = r.json()
            return {"ok": True, "worker_id": d.get("worker_id", "?"),
                    "latency_ms": elapsed, "retry_count": d.get("retry_count", 0)}
        return {"ok": False, "latency_ms": elapsed, "status": r.status_code}
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return {"ok": False, "latency_ms": elapsed, "error": str(e)}


def print_level_summary(level: int, results: list[dict], wall: float) -> None:
    ok   = [r for r in results if r.get("ok")]
    fail = [r for r in results if not r.get("ok")]
    lats = sorted(r["latency_ms"] for r in ok)
    n    = len(lats)

    worker_counts: dict[str, int] = defaultdict(int)
    for r in ok:
        worker_counts[r["worker_id"][:8]] += 1

    print(f"\n{'='*60}")
    print(f"  LEVEL: {level} concurrent users")
    print(f"{'='*60}")
    print(f"  Successful   : {len(ok)} / {level}")
    print(f"  Failed       : {len(fail)}")
    print(f"  Success rate : {len(ok)/level*100:.1f}%")
    print(f"  Throughput   : {level/wall:.2f} req/s")
    print(f"  Wall time    : {wall:.1f}s")
    if n:
        print(f"\n  Latency")
        print(f"    mean : {statistics.mean(lats)/1000:.2f}s")
        print(f"    p50  : {statistics.median(lats)/1000:.2f}s")
        print(f"    p95  : {lats[int(n*0.95)]/1000:.2f}s")
        print(f"    p99  : {lats[min(int(n*0.99),n-1)]/1000:.2f}s")
        print(f"    min  : {lats[0]/1000:.2f}s")
        print(f"    max  : {lats[-1]/1000:.2f}s")
    if worker_counts:
        print(f"\n  Worker distribution")
        for wid, count in sorted(worker_counts.items(), key=lambda x: -x[1]):
            print(f"    {wid} : {count}")
    if fail:
        by_err: dict[str, int] = {}
        for r in fail:
            key = f"HTTP {r.get('status','?')}" if "status" in r else r.get("error","?")[:60]
            by_err[key] = by_err.get(key, 0) + 1
        print(f"\n  Failures")
        for err, count in sorted(by_err.items(), key=lambda x: -x[1]):
            print(f"    [{count}x] {err}")


async def run_level(client: httpx.AsyncClient, level: int) -> list[dict]:
    print(f"\n  Firing {level} requests simultaneously...")
    start = time.monotonic()
    results = await asyncio.gather(*[
        send(client, i + 1, QUERIES[i % len(QUERIES)])
        for i in range(level)
    ])
    wall = time.monotonic() - start
    print_level_summary(level, list(results), wall)
    return list(results)


async def main(levels: list[int]) -> None:
    print(f"\n{'='*60}")
    print(f"  PDF Load Test")
    print(f"  Levels: {' → '.join(str(l) for l in levels)}")
    print(f"  Coordinator: {COORDINATOR_URL}")
    print(f"{'='*60}")

    async with httpx.AsyncClient() as client:
        r = await client.get(f"{COORDINATOR_URL}/health", timeout=10)
        health = r.json()
        print(f"\n  Cluster: {health['healthy_workers']} healthy workers")

        all_results: list[dict] = []
        for level in levels:
            results = await run_level(client, level)
            all_results.extend(results)
            if level != levels[-1]:
                print("\n  Cooling down 10s before next level...")
                await asyncio.sleep(10)

    ok   = [r for r in all_results if r.get("ok")]
    lats = sorted(r["latency_ms"] for r in ok)
    n    = len(lats)
    total = len(all_results)

    print(f"\n{'='*60}")
    print(f"  OVERALL SUMMARY")
    print(f"{'='*60}")
    print(f"  Total requests : {total}")
    print(f"  Successful     : {len(ok)}")
    print(f"  Success rate   : {len(ok)/total*100:.1f}%")
    if n:
        print(f"  Overall p95    : {lats[int(n*0.95)]/1000:.2f}s")
        print(f"  Overall p99    : {lats[min(int(n*0.99),n-1)]/1000:.2f}s")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", nargs="+", type=int, default=[100, 500, 1000])
    args = parser.parse_args()
    asyncio.run(main(args.levels))
