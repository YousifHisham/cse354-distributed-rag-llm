from __future__ import annotations

import asyncio
import logging
import time
import uuid

from .models import WorkerHeartbeat, WorkerInfo, WorkerMetrics, WorkerStatus

logger = logging.getLogger(__name__)


class WorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[str, WorkerInfo] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        address: str,
        name: str | None = None,
        model: str | None = None,
        backend_available: bool = False,
    ) -> WorkerInfo:
        async with self._lock:
            existing = self._find_existing(address, name)
            if existing is not None:
                existing.address = address
                existing.name = name
                existing.model = model
                existing.backend_available = backend_available
                existing.status = WorkerStatus.healthy
                existing.last_heartbeat = time.time()
                existing.assigned_tasks = 0
                logger.info(
                    "[registry] %s re-registered at %s name=%s model=%s backend=%s",
                    existing.worker_id,
                    address,
                    name,
                    model,
                    backend_available,
                )
                return existing

            worker_id = self._new_worker_id()
            info = WorkerInfo(
                worker_id=worker_id,
                address=address,
                name=name,
                model=model,
                status=WorkerStatus.healthy,
                backend_available=backend_available,
                last_heartbeat=time.time(),
                registered_at=time.time(),
            )
            self._workers[worker_id] = info
            logger.info(
                "[registry] %s registered at %s name=%s model=%s backend=%s",
                worker_id,
                address,
                name,
                model,
                backend_available,
            )
            return info

    def _find_existing(self, address: str, name: str | None) -> WorkerInfo | None:
        for worker in self._workers.values():
            if worker.address == address:
                return worker
        if name is not None:
            for worker in self._workers.values():
                if worker.name == name:
                    return worker
        return None

    def _new_worker_id(self) -> str:
        while True:
            worker_id = f"worker-{uuid.uuid4().hex[:8]}"
            if worker_id not in self._workers:
                return worker_id

    async def update_heartbeat(self, heartbeat: WorkerHeartbeat) -> bool:
        async with self._lock:
            if heartbeat.worker_id not in self._workers:
                return False
            worker = self._workers[heartbeat.worker_id]
            worker.last_heartbeat = time.time()
            worker.backend_available = heartbeat.backend_available
            if worker.last_metrics is not None:
                worker.last_metrics.active_tasks = heartbeat.active_tasks
                worker.last_metrics.gpu_utilization = heartbeat.gpu_utilization
                worker.last_metrics.vram_used_gb = heartbeat.vram_used_gb
                worker.last_metrics.vram_total_gb = heartbeat.vram_total_gb
                worker.last_metrics.gpu_temperature_c = heartbeat.gpu_temperature_c
                worker.last_metrics.backend_available = heartbeat.backend_available
            if worker.status == WorkerStatus.unavailable:
                worker.status = WorkerStatus.healthy
                logger.info("[registry] %s restored to healthy", heartbeat.worker_id)
            return True

    async def update_metrics(self, worker_id: str, metrics: WorkerMetrics) -> bool:
        async with self._lock:
            if worker_id not in self._workers:
                return False
            worker = self._workers[worker_id]
            worker.last_metrics = metrics
            worker.backend_available = metrics.backend_available
            return True

    async def get_all_healthy(self) -> list[WorkerInfo]:
        async with self._lock:
            return [
                w for w in self._workers.values()
                if w.status == WorkerStatus.healthy and w.backend_available
            ]

    async def increment_assigned(self, worker_id: str) -> None:
        async with self._lock:
            if worker_id in self._workers:
                self._workers[worker_id].assigned_tasks += 1

    async def decrement_assigned(self, worker_id: str) -> None:
        async with self._lock:
            if worker_id in self._workers:
                worker = self._workers[worker_id]
                worker.assigned_tasks = max(0, worker.assigned_tasks - 1)

    async def get_all(self) -> list[WorkerInfo]:
        async with self._lock:
            return list(self._workers.values())

    async def check_staleness(self, staleness_seconds: float) -> None:
        async with self._lock:
            now = time.time()
            for worker in self._workers.values():
                if worker.status == WorkerStatus.healthy and worker.last_heartbeat is not None:
                    age = now - worker.last_heartbeat
                    if age > staleness_seconds:
                        worker.status = WorkerStatus.unavailable
                        logger.warning(
                            "[registry] %s marked unavailable (last heartbeat %.1fs ago)",
                            worker.worker_id, age,
                        )
