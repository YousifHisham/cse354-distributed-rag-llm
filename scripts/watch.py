#!/usr/bin/env python3
"""
Live cluster dashboard. Polls /debug/state every second and refreshes the view.

Usage:
  python3 scripts/watch.py
  python3 scripts/watch.py http://localhost
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone

import httpx

MASTER_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost").rstrip("/")
REFRESH = 1.0

RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

BAR_WIDTH = 20


def bar(value: float, max_val: float = 1.0, width: int = BAR_WIDTH) -> str:
    filled = int(min(value / max_val, 1.0) * width)
    pct = value / max_val if max_val else 0
    color = GREEN if pct < 0.6 else YELLOW if pct < 0.85 else RED
    return color + "█" * filled + DIM + "░" * (width - filled) + RESET


def since(ts: float | None) -> str:
    if ts is None:
        return "never"
    secs = time.time() - ts
    return f"{secs:.1f}s ago"


async def fetch(client: httpx.AsyncClient) -> dict | None:
    try:
        r = await client.get(f"{MASTER_URL}/debug/state", timeout=4)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def render(data: dict, tick: int) -> str:
    lines = []
    now = datetime.now().strftime("%H:%M:%S")
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[tick % 10]

    if "error" in data:
        lines.append(f"{RED}{BOLD}  Cannot reach master: {data['error']}{RESET}")
        lines.append(f"  {DIM}Retrying...{RESET}")
        return "\n".join(lines)

    workers       = data.get("workers", [])
    strategy      = data.get("strategy", "?")
    queue         = data.get("queue_depth", 0)
    healthy       = data.get("healthy_workers", 0)
    total         = data.get("total_workers", 0)
    active_reqs   = data.get("active_requests", 0)
    unhealthy     = total - healthy

    lines.append(f"{BOLD}  {spinner} Distributed Inference Cluster{RESET}  {DIM}{now}{RESET}")
    lines.append(
        f"  Strategy: {CYAN}{BOLD}{strategy}{RESET}   "
        f"Queue: {YELLOW if queue > 0 else ''}{BOLD}{queue}{RESET}   "
        f"Workers: {GREEN}{healthy}✓{RESET} {RED}{unhealthy}✗{RESET}   "
        f"In-flight: {BOLD}{active_reqs}{RESET}"
    )
    lines.append(f"  {'─'*70}")

    if not workers:
        lines.append(f"  {YELLOW}No workers registered yet.{RESET}")
        lines.append(f"  {DIM}Run: ./scripts/start_worker.sh <ngrok-url> gpu-worker-1{RESET}")
        return "\n".join(lines)

    for w in workers:
        wid     = w.get("worker_id", "?")[:8]
        address = w.get("address", "?")
        name    = w.get("name") or wid
        status  = w.get("status", "?").upper()
        active  = w.get("assigned_tasks", 0)
        hb      = since(w.get("last_heartbeat"))
        m       = w.get("last_metrics") or {}
        gpu_pct = m.get("gpu_utilization", 0.0)
        vram_u  = m.get("vram_used_gb", 0.0)
        vram_t  = m.get("vram_total_gb", 0.0)
        temp    = m.get("gpu_temperature_c", 0.0)
        avg_lat = m.get("avg_latency_ms", 0.0)
        col     = GREEN if status == "HEALTHY" else RED

        lines.append(
            f"  {col}{BOLD}[{status:11s}]{RESET}  {BOLD}{name}{RESET}  {DIM}{address}{RESET}"
        )
        lines.append(
            f"              active={BOLD}{active:3d}{RESET}  "
            f"gpu={bar(gpu_pct)} {gpu_pct*100:4.0f}%  "
            f"vram={vram_u:.1f}/{vram_t:.0f}GB  "
            f"temp={temp:.0f}°C  "
            f"avg_lat={avg_lat:.0f}ms  "
            f"hb={DIM}{hb}{RESET}"
        )

    lines.append(f"  {'─'*70}")
    lines.append(f"  {DIM}Ctrl+C to exit  ·  refresh {REFRESH}s  ·  {MASTER_URL}{RESET}")
    return "\n".join(lines)


async def main() -> None:
    tick = 0
    async with httpx.AsyncClient() as client:
        while True:
            data = await fetch(client)
            output = render(data, tick)
            print("\033[H\033[J", end="")
            print(output)
            tick += 1
            await asyncio.sleep(REFRESH)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
