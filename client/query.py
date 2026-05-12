#!/usr/bin/env python3
"""
Send a single query and print the result.

Usage:
  python3 scripts/query.py
  python3 scripts/query.py "What is the CAP theorem?"
"""
import json
import os
import sys
import urllib.request

MASTER_URL = os.environ.get("COORDINATOR_URL", "http://localhost")
QUERY = sys.argv[1] if len(sys.argv) > 1 else "What is distributed computing?"

payload = json.dumps({"query": QUERY}).encode()
req = urllib.request.Request(
    f"{MASTER_URL}/query",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)

print(f"Query: {QUERY}\n")
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    print(f"Answer:   {data['answer']}")
    print(f"Worker:   {data['worker_id']}")
    print(f"Latency:  {data['latency_ms']/1000:.2f}s")
    print(f"Retries:  {data['retry_count']}")
except urllib.error.HTTPError as e:
    print(f"Error {e.code}: {e.read().decode()}")
except Exception as e:
    print(f"Failed: {e}")
