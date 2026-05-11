from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .models import WorkerInfo

logger = logging.getLogger(__name__)


class Strategy(ABC):
    @abstractmethod
    def select(self, workers: list[WorkerInfo]) -> WorkerInfo:
        ...

    @property
    @abstractmethod
    def label(self) -> str:
        ...


class LeastTasksStrategy(Strategy):
    label = "least_tasks"

    def select(self, workers: list[WorkerInfo]) -> WorkerInfo:
        def key(w: WorkerInfo) -> float:
            return float(_active_tasks(w))

        chosen = min(workers, key=key)
        metric = _active_tasks(chosen)
        logger.info("[balancer] least_tasks → %s (active_tasks=%s)", chosen.worker_id, metric)
        return chosen


class LowestResourceStrategy(Strategy):
    label = "lowest_resource"

    def select(self, workers: list[WorkerInfo]) -> WorkerInfo:
        def key(w: WorkerInfo) -> float:
            return w.last_metrics.resource_utilization if w.last_metrics else float("inf")

        chosen = min(workers, key=key)
        metric = f"{chosen.last_metrics.resource_utilization:.2f}" if chosen.last_metrics else "N/A"
        logger.info("[balancer] lowest_resource → %s (utilization=%s)", chosen.worker_id, metric)
        return chosen


class FastestResponseStrategy(Strategy):
    label = "fastest_response"

    def select(self, workers: list[WorkerInfo]) -> WorkerInfo:
        def key(w: WorkerInfo) -> float:
            if w.last_metrics is None or w.last_metrics.avg_latency_ms == 0.0:
                return 0.0
            return w.last_metrics.avg_latency_ms

        chosen = min(workers, key=key)
        metric = f"{chosen.last_metrics.avg_latency_ms:.0f}ms" if chosen.last_metrics else "N/A"
        logger.info("[balancer] fastest_response → %s (avg_latency=%s)", chosen.worker_id, metric)
        return chosen


class GpuAwareStrategy(Strategy):
    label = "gpu_aware"

    def select(self, workers: list[WorkerInfo]) -> WorkerInfo:
        def key(w: WorkerInfo) -> float:
            if w.last_metrics is None:
                return float(_active_tasks(w))

            m = w.last_metrics
            vram_ratio = (m.vram_used_gb / m.vram_total_gb) if m.vram_total_gb > 0 else 0.0
            latency_score = min(m.avg_latency_ms / 30_000.0, 1.0) if m.avg_latency_ms > 0 else 0.0
            active_score = min(float(_active_tasks(w)) / 10.0, 1.0)

            return (
                active_score * 0.35
                + m.gpu_utilization * 0.30
                + vram_ratio * 0.20
                + latency_score * 0.15
            )

        chosen = min(workers, key=key)
        logger.info("[balancer] gpu_aware → %s (score=%.3f)", chosen.worker_id, key(chosen))
        return chosen


def _active_tasks(worker: WorkerInfo) -> int:
    reported = worker.last_metrics.active_tasks if worker.last_metrics else 0
    return max(reported, worker.assigned_tasks)


_STRATEGIES: dict[str, Strategy] = {
    "least_tasks": LeastTasksStrategy(),
    "lowest_resource": LowestResourceStrategy(),
    "fastest_response": FastestResponseStrategy(),
    "gpu_aware": GpuAwareStrategy(),
}


def get_strategy(name: str) -> Strategy:
    if name not in _STRATEGIES:
        raise ValueError(
            f"Unknown LB_STRATEGY '{name}'. Valid values: {list(_STRATEGIES)}"
        )
    return _STRATEGIES[name]
