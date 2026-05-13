from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


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
    backend_available: bool = False
    timestamp: float


class WorkerHeartbeat(BaseModel):
    worker_id: str
    active_tasks: int
    gpu_utilization: float = 0.0  # 0.0 – 1.0
    vram_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    gpu_temperature_c: float = 0.0
    backend_available: bool = False
    timestamp: float


class WorkerInfo(BaseModel):
    worker_id: str
    address: str
    name: Optional[str] = None
    model: Optional[str] = None
    status: WorkerStatus = WorkerStatus.healthy
    backend_available: bool = False
    last_metrics: Optional[WorkerMetrics] = None
    last_heartbeat: Optional[float] = None
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
    worker_queue_wait_ms: float = 0.0
    ollama_latency_ms: float = 0.0
    prompt_eval_count: int = 0
    eval_count: int = 0
    tokens_per_second: float = 0.0


class Response(BaseModel):
    request_id: str
    answer: str
    latency_ms: float
    worker_id: str
    retry_count: int
    rag_used: bool = False
    rag_context_chars: int = 0
    rag_sources: list[str] = Field(default_factory=list)
    rag_latency_ms: float = 0.0
    queue_wait_ms: float = 0.0
    worker_latency_ms: float = 0.0
    worker_queue_wait_ms: float = 0.0
    ollama_latency_ms: float = 0.0
    prompt_eval_count: int = 0
    eval_count: int = 0
    tokens_per_second: float = 0.0
