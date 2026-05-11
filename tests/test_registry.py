from __future__ import annotations

import time

import pytest

from control.coordinator.models import WorkerHeartbeat, WorkerMetrics, WorkerStatus
from control.coordinator.registry import WorkerRegistry


@pytest.mark.asyncio
async def test_register_assigns_unique_worker_ids():
    registry = WorkerRegistry()

    first = await registry.register("http://worker:8001", name="gpu-a")
    second = await registry.register("http://worker:8001", name="gpu-a")

    assert first.worker_id.startswith("worker-")
    assert second.worker_id.startswith("worker-")
    assert first.worker_id != second.worker_id
    assert first.name == "gpu-a"


@pytest.mark.asyncio
async def test_heartbeat_marks_unavailable_then_recovers():
    registry = WorkerRegistry()
    worker = await registry.register("http://worker-1:8001")

    await registry.check_staleness(0.0)
    stored = (await registry.get_all())[0]
    assert stored.status == WorkerStatus.unavailable

    await registry.update_heartbeat(
        WorkerHeartbeat(
            worker_id=worker.worker_id,
            active_tasks=0,
            timestamp=time.time(),
        )
    )
    worker = (await registry.get_all())[0]
    assert worker.status == WorkerStatus.healthy


@pytest.mark.asyncio
async def test_metrics_do_not_recover_unavailable_without_heartbeat():
    registry = WorkerRegistry()
    worker = await registry.register("http://worker-1:8001")
    await registry.check_staleness(0.0)

    await registry.update_metrics(
        worker.worker_id,
        WorkerMetrics(
            worker_id=worker.worker_id,
            active_tasks=1,
            resource_utilization=0.5,
            avg_latency_ms=100,
            completed_tasks=1,
            failed_tasks=0,
            timestamp=time.time(),
        ),
    )

    worker = (await registry.get_all())[0]
    assert worker.status == WorkerStatus.unavailable
    assert worker.last_metrics is not None


@pytest.mark.asyncio
async def test_assigned_task_tracking_never_goes_negative():
    registry = WorkerRegistry()
    worker = await registry.register("http://worker-1:8001")

    await registry.increment_assigned(worker.worker_id)
    await registry.decrement_assigned(worker.worker_id)
    await registry.decrement_assigned(worker.worker_id)

    worker = (await registry.get_all())[0]
    assert worker.assigned_tasks == 0
