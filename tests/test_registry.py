from __future__ import annotations

import time

import pytest

from control.coordinator.models import WorkerHeartbeat, WorkerMetrics, WorkerStatus
from control.coordinator.registry import WorkerRegistry


@pytest.mark.asyncio
async def test_register_reuses_worker_id_for_same_worker():
    registry = WorkerRegistry()

    first = await registry.register("http://worker:8001", name="gpu-a")
    second = await registry.register("http://worker:8001", name="gpu-a")

    assert first.worker_id.startswith("worker-")
    assert second.worker_id.startswith("worker-")
    assert first.worker_id == second.worker_id
    assert first.name == "gpu-a"
    assert len(await registry.get_all()) == 1


@pytest.mark.asyncio
async def test_register_assigns_unique_worker_ids_for_different_workers():
    registry = WorkerRegistry()

    first = await registry.register("http://worker-a:8001", name="gpu-a")
    second = await registry.register("http://worker-b:8001", name="gpu-b")

    assert first.worker_id != second.worker_id
    assert len(await registry.get_all()) == 2


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
async def test_worker_without_backend_is_not_schedulable():
    registry = WorkerRegistry()
    worker = await registry.register("http://worker-1:8001")

    await registry.update_heartbeat(
        WorkerHeartbeat(
            worker_id=worker.worker_id,
            active_tasks=0,
            backend_available=False,
            timestamp=time.time(),
        )
    )

    assert await registry.get_all_healthy() == []

    await registry.update_heartbeat(
        WorkerHeartbeat(
            worker_id=worker.worker_id,
            active_tasks=0,
            backend_available=True,
            timestamp=time.time(),
        )
    )

    assert [w.worker_id for w in await registry.get_all_healthy()] == [worker.worker_id]


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
