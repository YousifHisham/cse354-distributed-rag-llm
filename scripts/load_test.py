#!/usr/bin/env python3
"""
Fire N requests simultaneously and print detailed per-request output plus a summary.

Usage:
  python3 scripts/load_test.py
  python3 scripts/load_test.py --requests 100
  python3 scripts/load_test.py --requests 100 --strategy gpu_aware
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

from common import QUERIES

MASTER_URL = os.environ.get("COORDINATOR_URL", "http://localhost")
ANSWER_CHARS = int(os.environ.get("ANSWER_CHARS", "300"))
SLOWEST_REQUESTS = int(os.environ.get("SLOWEST_REQUESTS", "5"))
CPU_LIKE_TOKENS_PER_SECOND = float(os.environ.get("CPU_LIKE_TOKENS_PER_SECOND", "10"))


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(int(len(sorted_values) * pct), len(sorted_values) - 1)
    return sorted_values[index]


def _answer_preview(answer: str) -> str:
    if ANSWER_CHARS <= 0 or len(answer) <= ANSWER_CHARS:
        return answer
    return f"{answer[:ANSWER_CHARS]}..."


def print_request_details(idx: int, query: str, data: dict, elapsed_ms: float) -> None:
    sources = data.get("rag_sources") or []
    token_speed = data.get("tokens_per_second", 0.0)
    speed_note = " CPU-LIKE?" if 0 < token_speed < CPU_LIKE_TOKENS_PER_SECOND else ""
    print(f"  [{idx:4d}] OK")
    print(f"         request_id={data.get('request_id', '?')}")
    print(f"         worker={data.get('worker_id', '?')} latency={elapsed_ms/1000:.2f}s retries={data.get('retry_count', 0)}")
    print(
        "         timing="
        f"rag={data.get('rag_latency_ms', 0)/1000:.2f}s "
        f"queue={data.get('queue_wait_ms', 0)/1000:.2f}s "
        f"worker={data.get('worker_latency_ms', 0)/1000:.2f}s "
        f"worker_wait={data.get('worker_queue_wait_ms', 0)/1000:.2f}s "
        f"ollama={data.get('ollama_latency_ms', 0)/1000:.2f}s"
    )
    print(
        f"         tokens=prompt:{data.get('prompt_eval_count', 0)} "
        f"generated:{data.get('eval_count', 0)} "
        f"speed={token_speed:.1f} tok/s{speed_note}"
    )
    print(f"         rag={'yes' if data.get('rag_used') else 'no'} context={data.get('rag_context_chars', 0)} chars sources={', '.join(sources) if sources else 'none'}")
    print(f"         Q: {query}")
    print(f"         A: {_answer_preview(data.get('answer', ''))}")
    print()


async def send(client: httpx.AsyncClient, idx: int, query: str) -> dict:
    start = time.monotonic()
    try:
        r = await client.post(f"{MASTER_URL}/query", json={"query": query})
        elapsed = (time.monotonic() - start) * 1000
        if r.status_code == 200:
            d = r.json()
            print_request_details(idx, query, d, elapsed)
            return {
                "ok": True,
                "request_id": d.get("request_id"),
                "query": query,
                "answer": d.get("answer", ""),
                "worker_id": d.get("worker_id", "?"),
                "latency_ms": elapsed,
                "retry_count": d.get("retry_count", 0),
                "rag_used": d.get("rag_used", False),
                "rag_context_chars": d.get("rag_context_chars", 0),
                "rag_sources": d.get("rag_sources", []),
                "rag_latency_ms": d.get("rag_latency_ms", 0.0),
                "queue_wait_ms": d.get("queue_wait_ms", 0.0),
                "worker_latency_ms": d.get("worker_latency_ms", 0.0),
                "worker_queue_wait_ms": d.get("worker_queue_wait_ms", 0.0),
                "ollama_latency_ms": d.get("ollama_latency_ms", 0.0),
                "prompt_eval_count": d.get("prompt_eval_count", 0),
                "eval_count": d.get("eval_count", 0),
                "tokens_per_second": d.get("tokens_per_second", 0.0),
            }
        print(f"  [{idx:4d}] ERR    status={r.status_code}  lat={elapsed/1000:.2f}s")
        return {"ok": False, "query": query, "latency_ms": elapsed, "status": r.status_code}
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        print(f"  [{idx:4d}] FAIL   {e}")
        return {"ok": False, "query": query, "latency_ms": elapsed, "error": str(e)}


async def main(total: int, strategy: str | None, out_dir: str | None, label: str | None) -> None:
    started_at = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient(timeout=None) as client:
        if strategy:
            await client.post(f"{MASTER_URL}/config/strategy", json={"strategy": strategy})
            print(f"Strategy set to: {strategy}")

        r = await client.get(f"{MASTER_URL}/health")
        health = r.json()
        active_strategy = strategy
        if not active_strategy:
            try:
                rs = await client.get(f"{MASTER_URL}/config/strategy")
                active_strategy = rs.json().get("strategy")
            except Exception:
                pass

        print(f"\nCluster: {health['healthy_workers']} healthy workers")
        print(f"Total requests: {total}")
        print(f"Actual simultaneous requests: {total}")
        print("Concurrency mode: all requests launched at once\n")

        start = time.monotonic()
        results = await asyncio.gather(*[
            send(client, i + 1, QUERIES[i % len(QUERIES)])
            for i in range(total)
        ])
        wall = time.monotonic() - start

    ok = [r for r in results if r.get("ok")]
    fail = [r for r in results if not r.get("ok")]
    lats = sorted(r["latency_ms"] for r in ok)
    n = len(lats)

    worker_counts: dict[str, int] = defaultdict(int)
    worker_latencies: dict[str, list[float]] = defaultdict(list)
    worker_retries: dict[str, int] = defaultdict(int)
    worker_rag_used: dict[str, int] = defaultdict(int)
    source_counts: dict[str, int] = defaultdict(int)
    for r in ok:
        worker_id = r["worker_id"][:8]
        worker_counts[worker_id] += 1
        worker_latencies[worker_id].append(r["latency_ms"])
        worker_retries[worker_id] += r.get("retry_count", 0)
        if r.get("rag_used"):
            worker_rag_used[worker_id] += 1
        for source in r.get("rag_sources", []):
            source_counts[source] += 1

    total_retries = sum(r.get("retry_count", 0) for r in ok)
    retried_requests = sum(1 for r in ok if r.get("retry_count", 0) > 0)
    rag_count = sum(1 for r in ok if r.get("rag_used"))
    rag_context_sizes = [r.get("rag_context_chars", 0) for r in ok]
    rag_lats = sorted(r.get("rag_latency_ms", 0.0) for r in ok)
    queue_lats = sorted(r.get("queue_wait_ms", 0.0) for r in ok)
    worker_lats = sorted(r.get("worker_latency_ms", 0.0) for r in ok)
    worker_wait_lats = sorted(r.get("worker_queue_wait_ms", 0.0) for r in ok)
    ollama_lats = sorted(r.get("ollama_latency_ms", 0.0) for r in ok)
    token_speeds = [r.get("tokens_per_second", 0.0) for r in ok if r.get("tokens_per_second", 0.0) > 0]
    cpu_like = [r for r in ok if 0 < r.get("tokens_per_second", 0.0) < CPU_LIKE_TOKENS_PER_SECOND]
    slowest = sorted(ok, key=lambda r: r["latency_ms"], reverse=True)[:SLOWEST_REQUESTS]

    print(f"\n{'='*55}")
    print("  DETAILED RESULTS")
    print(f"{'='*55}")
    print(f"  Run timestamp  : {started_at[:19].replace('T', ' ')}")
    print(f"  Total requests : {total}")
    print(f"  Actual concurrent requests : {total}")
    print("  Concurrency mode           : all requests launched at once")
    print(f"  Strategy       : {active_strategy or 'current'}")
    print(f"  Successful     : {len(ok)}")
    print(f"  Failed         : {len(fail)}")
    print(f"  Success rate   : {len(ok)/total*100:.1f}%")
    print(f"  Throughput     : {total/wall:.2f} req/s")
    print(f"  Wall time      : {wall:.1f}s")
    print(f"  Total retries  : {total_retries}")
    print(f"  Retried reqs   : {retried_requests}")
    if n:
        print("\n  Latency")
        print(f"    mean  : {statistics.mean(lats)/1000:.2f}s")
        print(f"    p50   : {statistics.median(lats)/1000:.2f}s")
        print(f"    p90   : {percentile(lats, 0.90)/1000:.2f}s")
        print(f"    p95   : {percentile(lats, 0.95)/1000:.2f}s")
        print(f"    p99   : {percentile(lats, 0.99)/1000:.2f}s")
        print(f"    min   : {lats[0]/1000:.2f}s")
        print(f"    max   : {lats[-1]/1000:.2f}s")
        print("\n  Timing breakdown")
        print(
            f"    RAG lookup       mean={statistics.mean(rag_lats)/1000:.2f}s "
            f"p95={percentile(rag_lats, 0.95)/1000:.2f}s"
        )
        print(
            f"    Coordinator queue mean={statistics.mean(queue_lats)/1000:.2f}s "
            f"p95={percentile(queue_lats, 0.95)/1000:.2f}s"
        )
        print(
            f"    Worker roundtrip  mean={statistics.mean(worker_lats)/1000:.2f}s "
            f"p95={percentile(worker_lats, 0.95)/1000:.2f}s"
        )
        print(
            f"    Worker wait       mean={statistics.mean(worker_wait_lats)/1000:.2f}s "
            f"p95={percentile(worker_wait_lats, 0.95)/1000:.2f}s"
        )
        print(
            f"    Ollama generate   mean={statistics.mean(ollama_lats)/1000:.2f}s "
            f"p95={percentile(ollama_lats, 0.95)/1000:.2f}s"
        )
        if token_speeds:
            print(f"    Token speed       mean={statistics.mean(token_speeds):.1f} tok/s")
            print(f"    CPU-like speeds   {len(cpu_like)}/{len(ok)} below {CPU_LIKE_TOKENS_PER_SECOND:.1f} tok/s")
    if worker_counts:
        print("\n  Worker distribution")
        for wid, count in sorted(worker_counts.items(), key=lambda x: -x[1]):
            wlats = sorted(worker_latencies[wid])
            avg = statistics.mean(wlats) / 1000 if wlats else 0.0
            p95 = percentile(wlats, 0.95) / 1000 if wlats else 0.0
            share = count / len(ok) * 100 if ok else 0.0
            print(
                f"    {wid} : {count} reqs ({share:.1f}%) "
                f"avg={avg:.2f}s p95={p95:.2f}s "
                f"retries={worker_retries[wid]} rag={worker_rag_used[wid]}"
            )
    print("\n  RAG")
    print(f"    used          : {rag_count}/{len(ok)} successful requests")
    if rag_context_sizes:
        print(f"    avg context   : {statistics.mean(rag_context_sizes)/1000:.1f}k chars")
        print(f"    min context   : {min(rag_context_sizes)} chars")
        print(f"    max context   : {max(rag_context_sizes)} chars")
    else:
        print("    avg context   : 0.0k chars")
    if source_counts:
        print("    top sources")
        for source, count in sorted(source_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"      {source} : {count}")
    if slowest:
        print("\n  Slowest requests")
        for r in slowest:
            print(
                f"    {r['latency_ms']/1000:.2f}s worker={r.get('worker_id', '?')[:8]} "
                f"retries={r.get('retry_count', 0)} rag={'yes' if r.get('rag_used') else 'no'} "
                f"query={r.get('query', '')[:80]}"
            )
    if fail:
        print("\n  Failures")
        by_err: dict[str, int] = {}
        for r in fail:
            key = f"HTTP {r.get('status','?')}" if "status" in r else r.get("error", "unknown")[:60]
            by_err[key] = by_err.get(key, 0) + 1
        for err, count in sorted(by_err.items(), key=lambda x: -x[1]):
            print(f"    [{count}x] {err}")
        print("\n  Failed request details")
        for r in fail[:10]:
            detail = f"HTTP {r.get('status')}" if "status" in r else r.get("error", "unknown")
            print(f"    {detail} lat={r['latency_ms']/1000:.2f}s query={r.get('query', '')[:80]}")
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
            "reported_users": total,
            "actual_simultaneous_requests": total,
            "concurrency_mode": "all_requests_launched_simultaneously",
            "total_requests": total,
            "successful_requests": len(ok),
            "failed_requests": len(fail),
            "throughput_rps": round(total / wall, 2) if wall > 0 else 0.0,
            "avg_latency_ms": round(statistics.mean(lats), 2) if lats else 0.0,
            "min_latency_ms": round(lats[0], 2) if lats else 0.0,
            "max_latency_ms": round(lats[-1], 2) if lats else 0.0,
            "p50_latency_ms": round(statistics.median(lats), 2) if lats else 0.0,
            "p90_latency_ms": round(percentile(lats, 0.90), 2) if lats else 0.0,
            "p95_latency_ms": round(percentile(lats, 0.95), 2) if lats else 0.0,
            "p99_latency_ms": round(percentile(lats, 0.99), 2) if lats else 0.0,
            "avg_rag_latency_ms": round(statistics.mean(rag_lats), 2) if lats else 0.0,
            "avg_queue_wait_ms": round(statistics.mean(queue_lats), 2) if lats else 0.0,
            "avg_worker_latency_ms": round(statistics.mean(worker_lats), 2) if lats else 0.0,
            "avg_worker_queue_wait_ms": round(statistics.mean(worker_wait_lats), 2) if lats else 0.0,
            "avg_ollama_latency_ms": round(statistics.mean(ollama_lats), 2) if lats else 0.0,
            "avg_tokens_per_second": round(statistics.mean(token_speeds), 2) if token_speeds else 0.0,
            "cpu_like_token_speed_requests": len(cpu_like),
            "cpu_like_token_speed_threshold": CPU_LIKE_TOKENS_PER_SECOND,
            "total_retries": total_retries,
            "retried_requests": retried_requests,
            "worker_distribution": dict(worker_counts),
            "worker_retry_totals": dict(worker_retries),
            "rag_source_counts": dict(source_counts),
            "rag_used_requests": rag_count,
            "avg_rag_context_chars": round(
                statistics.mean(rag_context_sizes), 2
            ) if ok else 0.0,
            "failure_statuses": sorted({r.get("status", 0) for r in fail}),
            "slowest_requests": [
                {
                    "request_id": r.get("request_id"),
                    "query": r.get("query"),
                    "worker_id": r.get("worker_id"),
                    "latency_ms": round(r.get("latency_ms", 0.0), 2),
                    "retry_count": r.get("retry_count", 0),
                    "rag_used": r.get("rag_used", False),
                }
                for r in slowest
            ],
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
