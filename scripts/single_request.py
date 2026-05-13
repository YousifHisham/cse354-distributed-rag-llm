#!/usr/bin/env python3
"""
Send a single query and print the result.

Usage:
  python3 scripts/single_request.py
  python3 scripts/single_request.py "What is the CAP theorem?"
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
    with urllib.request.urlopen(req, timeout=None) as resp:
        data = json.loads(resp.read())
    print(f"Answer:   {data['answer']}")
    print(f"Request:  {data.get('request_id', '?')}")
    print(f"Worker:   {data['worker_id']}")
    print(f"Latency:  {data['latency_ms']/1000:.2f}s")
    print(f"Retries:  {data['retry_count']}")
    print(f"RAG:      {'yes' if data.get('rag_used') else 'no'}")
    print(f"Context:  {data.get('rag_context_chars', 0)} chars")
    sources = data.get("rag_sources") or []
    print(f"Sources:  {', '.join(sources) if sources else 'none'}")
except urllib.error.HTTPError as e:
    print(f"Error {e.code}: {e.read().decode()}")
except Exception as e:
    print(f"Failed: {e}")
