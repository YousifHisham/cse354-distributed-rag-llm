from pydantic import BaseModel


class InferRequest(BaseModel):
    request_id: str
    prompt: str
    model: str


class InferResponse(BaseModel):
    request_id: str
    answer: str
    latency_ms: float


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


class WorkerRegistration(BaseModel):
    address: str
    name: str | None = None
    model: str | None = None


class WorkerRegistrationResponse(BaseModel):
    status: str
    worker_id: str
