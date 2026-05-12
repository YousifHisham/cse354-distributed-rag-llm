#!/usr/bin/env python3
"""
Fire N requests simultaneously and print a results summary.

Usage:
  python3 scripts/load_test.py
  python3 scripts/load_test.py --requests 100
  python3 scripts/load_test.py --requests 100 --strategy gpu_aware
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import os

import httpx

MASTER_URL = os.environ.get("COORDINATOR_URL", "http://localhost")

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
        r = await client.post(f"{MASTER_URL}/query", json={"query": query}, timeout=180)
        elapsed = (time.monotonic() - start) * 1000
        if r.status_code == 200:
            d = r.json()
            print(f"  [{idx:4d}] OK     worker={d.get('worker_id','?')[:8]}  lat={elapsed/1000:.2f}s  retries={d.get('retry_count',0)}")
            return {"ok": True, "worker_id": d.get("worker_id", "?"), "latency_ms": elapsed, "retry_count": d.get("retry_count", 0)}
        print(f"  [{idx:4d}] ERR    status={r.status_code}  lat={elapsed/1000:.2f}s")
        return {"ok": False, "latency_ms": elapsed, "status": r.status_code}
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        print(f"  [{idx:4d}] FAIL   {e}")
        return {"ok": False, "latency_ms": elapsed, "error": str(e)}


async def main(total: int, strategy: str | None, out_dir: str | None, label: str | None) -> None:
    started_at = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient() as client:
        if strategy:
            await client.post(f"{MASTER_URL}/config/strategy", json={"strategy": strategy}, timeout=5)
            print(f"Strategy set to: {strategy}")

        r = await client.get(f"{MASTER_URL}/health", timeout=5)
        health = r.json()
        active_strategy = strategy
        if not active_strategy:
            try:
                rs = await client.get(f"{MASTER_URL}/config/strategy", timeout=5)
                active_strategy = rs.json().get("strategy")
            except Exception:
                pass

        print(f"\nCluster: {health['healthy_workers']} healthy workers")
        print(f"Firing {total} requests simultaneously...\n")

        start = time.monotonic()
        results = await asyncio.gather(*[
            send(client, i + 1, QUERIES[i % len(QUERIES)])
            for i in range(total)
        ])
        wall = time.monotonic() - start

    ok   = [r for r in results if r.get("ok")]
    fail = [r for r in results if not r.get("ok")]
    lats = sorted(r["latency_ms"] for r in ok)
    n = len(lats)

    worker_counts: dict[str, int] = defaultdict(int)
    for r in ok:
        worker_counts[r["worker_id"][:8]] += 1

    print(f"\n{'='*55}")
    print(f"  RESULTS")
    print(f"{'='*55}")
    print(f"  Run timestamp  : {started_at[:19].replace('T', ' ')}")
    print(f"  Total requests : {total}")
    print(f"  Strategy       : {active_strategy or 'current'}")
    print(f"  Successful     : {len(ok)}")
    print(f"  Failed         : {len(fail)}")
    print(f"  Success rate   : {len(ok)/total*100:.1f}%")
    print(f"  Throughput     : {total/wall:.2f} req/s")
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
            key = f"HTTP {r.get('status','?')}" if "status" in r else r.get("error", "unknown")[:60]
            by_err[key] = by_err.get(key, 0) + 1
        for err, count in sorted(by_err.items(), key=lambda x: -x[1]):
            print(f"    [{count}x] {err}")
    print(f"{'='*55}\n")

    if out_dir:
        run_label = label or f"{active_strategy or 'run'}_{total}"
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        summary = {
            "label": run_label,
            "strategy": active_strategy,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(wall, 3),
            "concurrent_users": total,
            "total_requests": total,
            "successful_requests": len(ok),
            "failed_requests": len(fail),
            "throughput_rps": round(total / wall, 2) if wall > 0 else 0.0,
            "avg_latency_ms": round(statistics.mean(lats), 2) if lats else 0.0,
            "min_latency_ms": round(lats[0], 2) if lats else 0.0,
            "max_latency_ms": round(lats[-1], 2) if lats else 0.0,
            "p50_latency_ms": round(statistics.median(lats), 2) if lats else 0.0,
            "p95_latency_ms": round(lats[int(n * 0.95)], 2) if lats else 0.0,
            "p99_latency_ms": round(lats[min(int(n * 0.99), n - 1)], 2) if lats else 0.0,
            "worker_distribution": dict(worker_counts),
            "failure_statuses": sorted({r.get("status", 0) for r in fail}),
            "requests_file": str(out / f"{run_label}_requests.jsonl"),
            "errors_file": str(out / f"{run_label}_errors.jsonl"),
        }
        summary_path = out / f"{run_label}_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        print(f"  Summary saved : {summary_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--strategy", choices=["least_tasks", "lowest_resource", "fastest_response", "gpu_aware"])
    parser.add_argument("--out-dir", help="Directory to save summary JSON")
    parser.add_argument("--label", help="Label for saved files")
    args = parser.parse_args()
    asyncio.run(main(args.requests, args.strategy, args.out_dir, args.label))
