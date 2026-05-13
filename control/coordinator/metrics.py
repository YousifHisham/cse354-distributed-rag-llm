"""
Central Prometheus metrics registry for the coordinator.

All metrics are defined here and imported wherever they need to be updated.
The /prometheus endpoint in main.py serves the generated text.
"""
from prometheus_client import Counter, Gauge, Histogram

# ── Request metrics ───────────────────────────────────────────────────────────

requests_total = Counter(
    "coordinator_requests_total",
    "Total queries received by the coordinator",
)

requests_completed = Counter(
    "coordinator_requests_completed_total",
    "Queries that returned a successful answer",
)

requests_failed = Counter(
    "coordinator_requests_failed_total",
    "Queries that exhausted all retries and returned an error",
)

requests_retried = Counter(
    "coordinator_retries_total",
    "Individual retry attempts across all requests",
)

active_requests = Gauge(
    "coordinator_active_requests",
    "Requests currently in-flight (dispatched but not yet answered)",
)

queue_depth = Gauge(
    "coordinator_queue_depth",
    "Requests waiting in the coordinator scheduler queue",
)

request_latency = Histogram(
    "coordinator_request_latency_ms",
    "End-to-end request latency in milliseconds",
    buckets=[250, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 30_000, 60_000],
)

# ── Worker fleet metrics ──────────────────────────────────────────────────────

healthy_workers = Gauge(
    "coordinator_healthy_workers",
    "Number of workers currently in healthy state",
)

total_workers = Gauge(
    "coordinator_total_workers",
    "Total registered workers (healthy + unavailable)",
)

# ── Per-worker live metrics (label = worker_id) ───────────────────────────────

worker_active_tasks = Gauge(
    "worker_active_tasks",
    "Active tasks currently being processed by this worker",
    ["worker_id"],
)

worker_resource_utilization = Gauge(
    "worker_resource_utilization",
    "Current resource utilization reported by this worker (0.0 – 1.0)",
    ["worker_id"],
)

worker_avg_latency_ms = Gauge(
    "worker_avg_latency_ms",
    "Rolling average inference latency reported by this worker (ms)",
    ["worker_id"],
)

worker_completed_tasks = Gauge(
    "worker_completed_tasks_total",
    "Cumulative completed tasks reported by this worker since its startup",
    ["worker_id"],
)

worker_failed_tasks = Gauge(
    "worker_failed_tasks_total",
    "Cumulative failed tasks reported by this worker since its startup",
    ["worker_id"],
)

worker_status = Gauge(
    "worker_status",
    "Worker schedulable status: 1 = process healthy and backend available, 0 = unavailable",
    ["worker_id"],
)

worker_ollama_available = Gauge(
    "worker_ollama_available",
    "Whether the worker can reach its configured Ollama model: 1 = available, 0 = unavailable",
    ["worker_id"],
)

worker_gpu_utilization = Gauge(
    "worker_gpu_utilization",
    "GPU utilization reported by this worker (0.0 – 1.0)",
    ["worker_id"],
)

worker_vram_used_gb = Gauge(
    "worker_vram_used_gb",
    "VRAM currently used by this worker in GB",
    ["worker_id"],
)

worker_vram_total_gb = Gauge(
    "worker_vram_total_gb",
    "Total VRAM available on this worker in GB",
    ["worker_id"],
)

worker_gpu_temperature_c = Gauge(
    "worker_gpu_temperature_c",
    "GPU temperature reported by this worker in Celsius",
    ["worker_id"],
)
