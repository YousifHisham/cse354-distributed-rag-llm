#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

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
    fetch(base_url, "/prometheus", results_dir / f"{label}_prometheus.txt", results_dir)


def run_load(base_url: str, requests: int, strategy: str, results_dir: Path) -> int:
    console_path = results_dir / "console.log"
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
        f"fault_tolerance_{requests}",
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


def docker_start(worker: str) -> bool:
    proc = subprocess.run(
        ["docker", "compose", "start", worker],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("COORDINATOR_URL", "http://localhost"))
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--strategy", default="gpu_aware")
    parser.add_argument("--worker", default="worker-1")
    parser.add_argument("--results-dir")
    args = parser.parse_args()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results_dir = Path(args.results_dir or f"results/fault_tolerance_{run_id}")
    results_dir.mkdir(parents=True, exist_ok=True)

    log(results_dir, "Fault tolerance test started")
    log(results_dir, "Requirement: shut down a worker during execution and verify recovery")
    log(results_dir, f"Output directory: {results_dir}")
    snapshot(args.url, "before_failure", results_dir)

    print()
    print(f"This test will run {args.requests} simultaneous requests.")
    print("When the request output starts, open another terminal and run:")
    print()
    print(f"  docker compose stop {args.worker}")
    print()
    print("After the run finishes, the script will try to restart it with:")
    print()
    print(f"  docker compose start {args.worker}")
    print()
    input("Press Enter when you are ready to start the fault test...")

    status = run_load(args.url, args.requests, args.strategy, results_dir)
    snapshot(args.url, "after_failure_run", results_dir)

    log(results_dir, f"Restarting {args.worker} so the cluster can recover")
    if not docker_start(args.worker):
        log(results_dir, f"WARN: could not start {args.worker} automatically")
    time.sleep(5)
    snapshot(args.url, "after_recovery", results_dir)

    if status != 0:
        log(results_dir, f"Load client exited with status {status}. Check errors and recovery snapshots.")
    else:
        log(results_dir, "Fault tolerance load run completed without client-side failure.")

    log(results_dir, f"Main console output: {results_dir / 'console.log'}")
    log(results_dir, f"Summary JSON: {results_dir / f'fault_tolerance_{args.requests}_summary.json'}")
    log(results_dir, "Recovery evidence: before_failure_*, after_failure_run_*, after_recovery_*")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
