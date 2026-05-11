#!/usr/bin/env python3
"""
End-to-end cluster test. Shows per-worker breakdown, latencies, and strategy behavior.

Usage:
  python3 scripts/cluster_test.py
  python3 scripts/cluster_test.py --concurrency 10 --total 30
  python3 scripts/cluster_test.py --strategy gpu_aware
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from collections import defaultdict

import httpx

MASTER_URL = "http://localhost"

QUERIES = [
    "What is distributed computing?",
    "Explain fault tolerance in systems.",
    "What is load balancing?",
    "How does consensus work in distributed systems?",
    "What is the CAP theorem?",
    "Explain horizontal vs vertical scaling.",
    "What is a message queue?",
    "How do distributed databases handle replication?",
]


async def show_cluster_status(client: httpx.AsyncClient, master_url: str) -> bool:
    try:
        r = await client.get(f"{master_url}/debug/state", timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[ERROR] Cannot reach master at {master_url}: {e}")
        return False

    workers = data.get("workers", [])
    print(f"\n{'='*60}")
    print(f"  Cluster status — strategy: {data.get('strategy', '?')}")
    print(f"  healthy={data.get('healthy_workers', 0)}  total={data.get('total_workers', 0)}  queue={data.get('queue_depth', 0)}")
    print(f"{'='*60}")
    if not workers:
        print("  [!] No workers registered.")
        return False
    for w in workers:
        wid  = w["worker_id"][:8]
        name = w.get("name") or wid
        m    = w.get("last_metrics") or {}
        print(
            f"  [{w['status']:11s}] {name}  addr={w['address']}  "
            f"active={w.get('assigned_tasks', 0)}  "
            f"gpu={m.get('gpu_utilization', 0)*100:.0f}%  "
            f"avg_lat={m.get('avg_latency_ms', 0):.0f}ms"
        )
    print(f"{'='*60}\n")
    return True


async def send_one(client: httpx.AsyncClient, master_url: str, query: str, idx: int) -> dict:
    start = time.monotonic()
    try:
        r = await client.post(f"{master_url}/query", json={"query": query}, timeout=150)
        elapsed = (time.monotonic() - start) * 1000
        if r.status_code == 200:
            d = r.json()
            wid = (d.get("worker_id") or "?")[:8]
            print(f"  [{idx:3d}] OK    worker={wid}  lat={elapsed:.0f}ms  retries={d.get('retry_count', 0)}")
            return {"ok": True, "worker_id": d.get("worker_id", "?"), "latency_ms": elapsed, "retry_count": d.get("retry_count", 0)}
        else:
            print(f"  [{idx:3d}] ERR   status={r.status_code}  lat={elapsed:.0f}ms")
            return {"ok": False, "latency_ms": elapsed, "status_code": r.status_code}
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        print(f"  [{idx:3d}] FAIL  {e}")
        return {"ok": False, "latency_ms": elapsed, "exc": str(e)}


async def run_test(master_url: str, total: int, strategy: str | None) -> None:
    async with httpx.AsyncClient(timeout=150) as client:
        reachable = await show_cluster_status(client, master_url)
        if not reachable:
            sys.exit(1)

        if strategy:
            r = await client.post(f"{master_url}/config/strategy", json={"strategy": strategy}, timeout=5)
            if r.status_code == 200:
                print(f"Strategy set to: {strategy}\n")
            else:
                print(f"[WARN] Could not set strategy: {r.text}\n")

        print(f"Firing {total} requests simultaneously")
        print(f"{'-'*60}")

        results = []

        async def fire(idx: int, query: str) -> None:
            result = await send_one(client, master_url, query, idx)
            results.append(result)

        start = time.monotonic()
        await asyncio.gather(*[
            fire(i + 1, QUERIES[i % len(QUERIES)])
            for i in range(total)
        ])
        wall = time.monotonic() - start

        ok   = [r for r in results if r.get("ok")]
        fail = [r for r in results if not r.get("ok")]
        latencies = sorted(r["latency_ms"] for r in ok)

        print(f"\n{'='*60}")
        print("  Results")
        print(f"{'='*60}")
        print(f"  Total      : {total}")
        print(f"  Success    : {len(ok)}  ({len(ok)/total*100:.1f}%)")
        print(f"  Errors     : {len(fail)}")
        print(f"  Throughput : {total/wall:.2f} req/s")
        print(f"  Wall time  : {wall:.1f}s")

        if latencies:
            n = len(latencies)
            print(f"  mean       : {statistics.mean(latencies)/1000:.2f}s")
            print(f"  p50        : {statistics.median(latencies)/1000:.2f}s")
            print(f"  p95        : {latencies[int(n*0.95)]/1000:.2f}s")
            print(f"  p99        : {latencies[min(int(n*0.99), n-1)]/1000:.2f}s")
            print(f"  min        : {latencies[0]/1000:.2f}s")
            print(f"  max        : {latencies[-1]/1000:.2f}s")

        worker_counts: dict[str, int] = defaultdict(int)
        worker_lats:   dict[str, list[float]] = defaultdict(list)
        for r in ok:
            wid = r["worker_id"][:8]
            worker_counts[wid] += 1
            worker_lats[wid].append(r["latency_ms"])

        if worker_counts:
            print(f"\n  Per-worker distribution")
            for wid, count in sorted(worker_counts.items(), key=lambda x: -x[1]):
                avg = sum(worker_lats[wid]) / len(worker_lats[wid])
                print(f"    {wid}  requests={count}  avg_lat={avg/1000:.2f}s")

        if fail:
            print(f"\n  Failures")
            by_err: dict[str, int] = {}
            for r in fail:
                key = f"HTTP {r.get('status_code', '?')}" if "status_code" in r else r.get("exc", "unknown")[:80]
                by_err[key] = by_err.get(key, 0) + 1
            for err, count in sorted(by_err.items(), key=lambda x: -x[1]):
                print(f"    [{count}x] {err}")

        print(f"{'='*60}")

        await show_cluster_status(client, master_url)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=MASTER_URL)
    parser.add_argument("--total", type=int, default=15)
    parser.add_argument("--strategy", choices=["least_tasks", "lowest_resource", "fastest_response", "gpu_aware"])
    args = parser.parse_args()

    asyncio.run(run_test(args.url, args.total, args.strategy))


if __name__ == "__main__":
    main()
