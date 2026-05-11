from __future__ import annotations

import time

from control.coordinator.balancer import get_strategy
from control.coordinator.models import WorkerInfo, WorkerMetrics, WorkerStatus


def worker(
    worker_id: str,
    *,
    active_tasks: int,
    resource_utilization: float,
    avg_latency_ms: float,
    gpu_utilization: float = 0.0,
    vram_used_gb: float = 0.0,
    vram_total_gb: float = 0.0,
    assigned_tasks: int = 0,
) -> WorkerInfo:
    return WorkerInfo(
        worker_id=worker_id,
        address=f"http://{worker_id}:8001",
        status=WorkerStatus.healthy,
        assigned_tasks=assigned_tasks,
        registered_at=time.time(),
        last_metrics=WorkerMetrics(
            worker_id=worker_id,
            active_tasks=active_tasks,
            resource_utilization=resource_utilization,
            avg_latency_ms=avg_latency_ms,
            completed_tasks=0,
            failed_tasks=0,
            gpu_utilization=gpu_utilization,
            vram_used_gb=vram_used_gb,
            vram_total_gb=vram_total_gb,
            gpu_temperature_c=60.0,
            timestamp=time.time(),
        ),
    )


def test_least_tasks_uses_effective_assigned_tasks():
    workers = [
        worker("worker-a", active_tasks=1, assigned_tasks=8, resource_utilization=0.1, avg_latency_ms=100),
        worker("worker-b", active_tasks=3, assigned_tasks=0, resource_utilization=0.9, avg_latency_ms=500),
    ]

    assert get_strategy("least_tasks").select(workers).worker_id == "worker-b"


def test_lowest_resource_strategy():
    workers = [
        worker("worker-a", active_tasks=1, resource_utilization=0.8, avg_latency_ms=100),
        worker("worker-b", active_tasks=5, resource_utilization=0.2, avg_latency_ms=500),
    ]

    assert get_strategy("lowest_resource").select(workers).worker_id == "worker-b"


def test_fastest_response_strategy():
    workers = [
        worker("worker-a", active_tasks=1, resource_utilization=0.8, avg_latency_ms=900),
        worker("worker-b", active_tasks=5, resource_utilization=0.2, avg_latency_ms=200),
    ]

    assert get_strategy("fastest_response").select(workers).worker_id == "worker-b"


def test_gpu_aware_strategy_balances_gpu_and_vram_pressure():
    workers = [
        worker(
            "worker-hot",
            active_tasks=0,
            resource_utilization=0.1,
            avg_latency_ms=200,
            gpu_utilization=0.95,
            vram_used_gb=22,
            vram_total_gb=24,
        ),
        worker(
            "worker-cool",
            active_tasks=1,
            resource_utilization=0.2,
            avg_latency_ms=300,
            gpu_utilization=0.25,
            vram_used_gb=4,
            vram_total_gb=24,
        ),
    ]

    assert get_strategy("gpu_aware").select(workers).worker_id == "worker-cool"
