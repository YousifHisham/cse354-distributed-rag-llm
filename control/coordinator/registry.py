from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

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
    ) -> WorkerInfo:
        async with self._lock:
            worker_id = self._new_worker_id()
            info = WorkerInfo(
                worker_id=worker_id,
                address=address,
                name=name,
                model=model,
                status=WorkerStatus.healthy,
                last_heartbeat=time.time(),
                registered_at=time.time(),
            )
            self._workers[worker_id] = info
            logger.info("[registry] %s registered at %s name=%s model=%s", worker_id, address, name, model)
            return info

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
            if worker.last_metrics is not None:
                worker.last_metrics.active_tasks = heartbeat.active_tasks
                worker.last_metrics.gpu_utilization = heartbeat.gpu_utilization
                worker.last_metrics.vram_used_gb = heartbeat.vram_used_gb
                worker.last_metrics.vram_total_gb = heartbeat.vram_total_gb
                worker.last_metrics.gpu_temperature_c = heartbeat.gpu_temperature_c
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
            worker.last_metrics_at = time.time()
            return True

    async def get_all_healthy(self, staleness_seconds: float) -> list[WorkerInfo]:
        async with self._lock:
            return [
                w for w in self._workers.values()
                if w.status == WorkerStatus.healthy
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

    async def get_first_healthy(self, staleness_seconds: float) -> Optional[WorkerInfo]:
        workers = await self.get_all_healthy(staleness_seconds)
        return workers[0] if workers else None

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
