from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class RequestStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"


class WorkerStatus(str, Enum):
    healthy = "healthy"
    unavailable = "unavailable"


class WorkerMetrics(BaseModel):
    worker_id: str
    active_tasks: int
    resource_utilization: float  # 0.0 – 1.0
    avg_latency_ms: float
    completed_tasks: int
    failed_tasks: int
    gpu_utilization: float = 0.0  # 0.0 – 1.0
    vram_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    gpu_temperature_c: float = 0.0
    timestamp: float


class WorkerHeartbeat(BaseModel):
    worker_id: str
    active_tasks: int
    gpu_utilization: float = 0.0  # 0.0 – 1.0
    vram_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    gpu_temperature_c: float = 0.0
    timestamp: float


class WorkerInfo(BaseModel):
    worker_id: str
    address: str
    name: Optional[str] = None
    model: Optional[str] = None
    status: WorkerStatus = WorkerStatus.healthy
    last_metrics: Optional[WorkerMetrics] = None
    last_heartbeat: Optional[float] = None
    last_metrics_at: Optional[float] = None
    assigned_tasks: int = 0
    registered_at: float


class InferRequest(BaseModel):
    request_id: str
    prompt: str
    model: str


class InferResponse(BaseModel):
    request_id: str
    answer: str
    latency_ms: float


class Request(BaseModel):
    id: str
    query: str
    context: Optional[str] = None
    status: RequestStatus = RequestStatus.pending
    assigned_worker_id: Optional[str] = None
    retry_count: int = 0
    created_at: float
    dispatched_at: Optional[float] = None
    completed_at: Optional[float] = None


class Response(BaseModel):
    request_id: str
    answer: str
    latency_ms: float
    worker_id: str
    retry_count: int
