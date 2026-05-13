#!/usr/bin/env python3
"""
Tiny GPU metrics server for Thunder Compute nodes.

Run this on each Thunder Compute instance and expose the port. Local workers
poll THUNDER_METRICS_URL for real nvidia-smi GPU stats.
"""
import argparse
import json
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer


def collect() -> dict:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=3,
        )
        gpus = []
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                continue
            name, util, mem_used, mem_total, temp, power_draw, power_limit = parts
            gpus.append(
                {
                    "name": name,
                    "gpu_utilization": float(util) / 100.0,
                    "vram_used_gb": float(mem_used) / 1024.0,
                    "vram_total_gb": float(mem_total) / 1024.0,
                    "gpu_temperature_c": float(temp),
                    "power_draw_w": float(power_draw) if power_draw != "[N/A]" else 0.0,
                    "power_limit_w": float(power_limit) if power_limit != "[N/A]" else 0.0,
                }
            )
        return {"gpus": gpus, "error": None}
    except FileNotFoundError:
        return {"gpus": [], "error": "nvidia-smi not found"}
    except Exception as exc:
        return {"gpus": [], "error": str(exc)}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = collect()
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), Handler)
    print(f"[thunder-metrics] GPU metrics server running on 0.0.0.0:{args.port}")
    print("[thunder-metrics] GET / returns nvidia-smi JSON")
    server.serve_forever()


if __name__ == "__main__":
    main()
