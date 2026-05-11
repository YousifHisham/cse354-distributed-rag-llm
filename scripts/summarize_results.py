#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def load_summaries(results_dir: Path) -> list[dict]:
    summaries = []
    for path in sorted(results_dir.glob("*_summary.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_file"] = path.name
            summaries.append(data)
        except Exception:
            continue
    return summaries


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: summarize_results.py <results_dir>", file=sys.stderr)
        return 2

    results_dir = Path(sys.argv[1])
    summaries = load_summaries(results_dir)

    print("# Evaluation Summary")
    print()
    print(f"Results directory: `{results_dir}`")
    print()

    if not summaries:
        print("No `*_summary.json` files found.")
        return 0

    print("| Label | Strategy | Users | Requests | Success | Failed | Avg ms | P95 ms | P99 ms | RPS | Workers |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for item in summaries:
        workers = ", ".join(
            f"{worker}:{count}"
            for worker, count in sorted((item.get("worker_distribution") or {}).items())
        )
        print(
            "| {label} | {strategy} | {users} | {requests} | {success} | {failed} | "
            "{avg} | {p95} | {p99} | {rps} | {workers} |".format(
                label=item.get("label", item.get("_file", "")),
                strategy=item.get("strategy") or "current",
                users=item.get("concurrent_users", 0),
                requests=item.get("total_requests", 0),
                success=item.get("successful_requests", 0),
                failed=item.get("failed_requests", 0),
                avg=item.get("avg_latency_ms", 0),
                p95=item.get("p95_latency_ms", 0),
                p99=item.get("p99_latency_ms", 0),
                rps=item.get("throughput_rps", 0),
                workers=workers or "-",
            )
        )

    print()
    print("## Files")
    print()
    for item in summaries:
        print(f"- `{item.get('_file')}`")
        print(f"  - requests: `{Path(item.get('requests_file', '')).name}`")
        print(f"  - errors: `{Path(item.get('errors_file', '')).name}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
