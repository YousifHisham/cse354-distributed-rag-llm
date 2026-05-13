#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

STRATEGIES = ["least_tasks", "lowest_resource", "fastest_response", "gpu_aware"]
ROOT = Path(__file__).resolve().parents[1]


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def log(results_dir: Path, message: str) -> None:
    line = f"[{timestamp()}] {message}"
    print(line)
    with (results_dir / "run.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def fetch(base_url: str, path: str, out: Path, results_dir: Path) -> None:
    try:
        with urllib.request.urlopen(f"{base_url}{path}", timeout=20) as response:
            out.write_bytes(response.read())
        log(results_dir, f"Saved {path} -> {out}")
    except Exception as exc:
        log(results_dir, f"WARN: could not fetch {path}: {exc}")


def snapshot(base_url: str, label: str, results_dir: Path) -> None:
    fetch(base_url, "/health", results_dir / f"{label}_health.json", results_dir)
    fetch(base_url, "/workers", results_dir / f"{label}_workers.json", results_dir)
    fetch(base_url, "/debug/state", results_dir / f"{label}_debug_state.json", results_dir)
    fetch(base_url, "/config/strategy", results_dir / f"{label}_strategy.json", results_dir)
    fetch(base_url, "/prometheus", results_dir / f"{label}_prometheus.txt", results_dir)


def run_load(base_url: str, requests: int, strategy: str, results_dir: Path) -> int:
    console_path = results_dir / f"{strategy}_{requests}_console.log"
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "load_test.py"),
        "--requests",
        str(requests),
        "--strategy",
        strategy,
        "--out-dir",
        str(results_dir),
        "--label",
        f"{strategy}_{requests}",
    ]
    env = {**os.environ, "COORDINATOR_URL": base_url}
    with console_path.open("w", encoding="utf-8") as console:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            console.write(line)
        return proc.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("COORDINATOR_URL", "http://localhost"))
    parser.add_argument("--requests", type=int, default=int(os.environ.get("REQUESTS", "1000")))
    parser.add_argument("--results-dir", default=os.environ.get("RESULTS_DIR"))
    args = parser.parse_args()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results_dir = Path(args.results_dir or f"results/strategy_1000_{run_id}")
    results_dir.mkdir(parents=True, exist_ok=True)

    log(results_dir, "1000-request strategy test started")
    log(results_dir, f"Coordinator: {args.url}")
    log(results_dir, f"Requests per strategy: {args.requests}")
    log(results_dir, f"Strategies: {' '.join(STRATEGIES)}")
    log(results_dir, f"Output directory: {results_dir}")

    snapshot(args.url, "before", results_dir)

    exit_code = 0
    for strategy in STRATEGIES:
        log(results_dir, f"Running {args.requests} simultaneous requests with strategy={strategy}")
        status = run_load(args.url, args.requests, strategy, results_dir)
        if status != 0:
            exit_code = status
            log(results_dir, f"WARN: load test exited with status {status} for strategy={strategy}")
        snapshot(args.url, f"after_{strategy}", results_dir)

    snapshot(args.url, "after_all", results_dir)
    log(results_dir, "Done")
    log(results_dir, f"Summary JSON files: {results_dir}/*_{args.requests}_summary.json")
    log(results_dir, f"Console logs: {results_dir}/*_{args.requests}_console.log")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
