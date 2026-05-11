#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_WRITE_LOCK = threading.Lock()


@dataclass
class RequestRecord:
    request_number: int
    query: str
    ok: bool
    status: int
    latency_ms: float
    started_at: str
    finished_at: str
    strategy: str | None = None
    concurrent_users: int | None = None
    request_id: str | None = None
    worker_id: str | None = None
    retry_count: int | None = None
    error: str | None = None
    response_chars: int = 0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, sort_keys=True) + "\n")


def get_json(url: str, timeout: float) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return resp.status, json.loads(raw) if raw else {}


def post_json(url: str, payload: dict, timeout: float) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return resp.status, json.loads(raw) if raw else {}


def safe_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8")
        if not raw:
            return exc.reason
        data = json.loads(raw)
        return str(data.get("detail", raw))
    except Exception:
        return exc.reason


def send_query(
    request_number: int,
    base_url: str,
    query: str,
    timeout: float,
    strategy: str | None,
    concurrent_users: int,
) -> RequestRecord:
    started_at = utc_now()
    start = time.perf_counter()
    try:
        status, data = post_json(
            f"{base_url.rstrip('/')}/query",
            {"query": query},
            timeout,
        )
        latency_ms = (time.perf_counter() - start) * 1000
        answer = data.get("answer") or ""
        return RequestRecord(
            request_number=request_number,
            query=query,
            ok=200 <= status < 300,
            status=status,
            latency_ms=latency_ms,
            started_at=started_at,
            finished_at=utc_now(),
            strategy=strategy,
            concurrent_users=concurrent_users,
            request_id=data.get("request_id"),
            worker_id=data.get("worker_id"),
            retry_count=data.get("retry_count"),
            response_chars=len(answer),
        )
    except urllib.error.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestRecord(
            request_number=request_number,
            query=query,
            ok=False,
            status=exc.code,
            latency_ms=latency_ms,
            started_at=started_at,
            finished_at=utc_now(),
            strategy=strategy,
            concurrent_users=concurrent_users,
            error=safe_error_body(exc),
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestRecord(
            request_number=request_number,
            query=query,
            ok=False,
            status=0,
            latency_ms=latency_ms,
            started_at=started_at,
            finished_at=utc_now(),
            strategy=strategy,
            concurrent_users=concurrent_users,
            error=type(exc).__name__,
        )


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, int(round((pct / 100.0) * (len(values) - 1))))
    return values[index]


def read_queries(query: str, queries_file: str | None, total: int) -> list[str]:
    if not queries_file:
        return [query for _ in range(total)]

    lines = [
        line.strip()
        for line in Path(queries_file).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return [query for _ in range(total)]
    return [lines[i % len(lines)] for i in range(total)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Load test the distributed LLM coordinator.")
    parser.add_argument("--url", default="http://localhost", help="Coordinator/NGINX base URL")
    parser.add_argument("--users", type=int, default=100, help="Concurrent users")
    parser.add_argument("--requests", type=int, default=100, help="Total requests to send")
    parser.add_argument("--query", default="What is distributed computing?", help="Query text")
    parser.add_argument("--queries-file", help="Optional file containing one query per line")
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-request timeout seconds")
    parser.add_argument("--strategy", help="Optional strategy to set before the run")
    parser.add_argument("--out-dir", default="results/manual", help="Directory for result files")
    parser.add_argument("--label", help="Result file label; default is strategy_users")
    parser.add_argument("--verbose", action="store_true", help="Print one line per request")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    active_strategy = args.strategy
    if args.strategy:
        status, data = post_json(
            f"{base_url}/config/strategy",
            {"strategy": args.strategy},
            timeout=10.0,
        )
        active_strategy = data.get("strategy", args.strategy)
        print(f"[load] strategy set status={status} strategy={active_strategy}")
    else:
        try:
            _, data = get_json(f"{base_url}/config/strategy", timeout=10.0)
            active_strategy = data.get("strategy")
        except Exception:
            active_strategy = None

    label_strategy = active_strategy or "current"
    label = args.label or f"{label_strategy}_{args.users}"
    requests_path = out_dir / f"{label}_requests.jsonl"
    errors_path = out_dir / f"{label}_errors.jsonl"
    summary_path = out_dir / f"{label}_summary.json"

    for path in (requests_path, errors_path):
        if path.exists():
            path.unlink()

    print(
        f"[load] url={base_url} strategy={label_strategy} users={args.users} "
        f"requests={args.requests} label={label}"
    )
    print(f"[load] writing per-request data to {requests_path}")

    queries = read_queries(args.query, args.queries_file, args.requests)
    started_at = utc_now()
    started = time.perf_counter()
    records: list[RequestRecord] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.users) as executor:
        futures = [
            executor.submit(
                send_query,
                i + 1,
                base_url,
                queries[i],
                args.timeout,
                active_strategy,
                args.users,
            )
            for i in range(args.requests)
        ]
        for future in concurrent.futures.as_completed(futures):
            record = future.result()
            records.append(record)
            record_dict = asdict(record)
            append_jsonl(requests_path, record_dict)
            if not record.ok:
                append_jsonl(errors_path, record_dict)
            if args.verbose:
                status = "OK " if record.ok else "ERR"
                print(
                    f"[REQ {record.request_number:04d}] {status} "
                    f"{record.status} {record.latency_ms:.0f}ms "
                    f"worker={record.worker_id or '-'} retries={record.retry_count}"
                )

    elapsed = time.perf_counter() - started
    successes = [r for r in records if r.ok]
    failures = [r for r in records if not r.ok]
    latencies = [r.latency_ms for r in successes]
    by_worker: dict[str, int] = {}
    for record in successes:
        by_worker[record.worker_id or "unknown"] = by_worker.get(record.worker_id or "unknown", 0) + 1

    summary = {
        "url": base_url,
        "label": label,
        "strategy": active_strategy,
        "started_at": started_at,
        "finished_at": utc_now(),
        "duration_seconds": round(elapsed, 3),
        "concurrent_users": args.users,
        "total_requests": args.requests,
        "successful_requests": len(successes),
        "failed_requests": len(failures),
        "throughput_rps": round(args.requests / elapsed, 2) if elapsed > 0 else 0.0,
        "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "min_latency_ms": round(min(latencies), 2) if latencies else 0.0,
        "max_latency_ms": round(max(latencies), 2) if latencies else 0.0,
        "p50_latency_ms": round(percentile(latencies, 50), 2),
        "p95_latency_ms": round(percentile(latencies, 95), 2),
        "p99_latency_ms": round(percentile(latencies, 99), 2),
        "worker_distribution": by_worker,
        "failure_statuses": sorted({r.status for r in failures}),
        "requests_file": str(requests_path),
        "errors_file": str(errors_path),
    }
    write_json(summary_path, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"[load] summary saved to {summary_path}")
    if failures:
        print(f"[load] errors saved to {errors_path}", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
