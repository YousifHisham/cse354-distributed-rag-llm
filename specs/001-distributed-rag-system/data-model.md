# Data Model: Distributed RAG-Augmented LLM System

**Phase**: 1 | **Date**: 2026-05-11

Application models are plain Python dataclasses / Pydantic models. Runtime worker/request state is in-memory, while RAG vectors are stored in a separate Chroma vector database container.

---

## Entities

### Request

Represents one user query travelling through the system.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` (UUID4) | Unique identifier assigned by coordinator at intake |
| `query` | `str` | Original user question text |
| `context` | `str \| None` | RAG-retrieved context passages (populated after enrichment) |
| `status` | `RequestStatus` | `pending` → `in_progress` → `completed` \| `failed` |
| `assigned_worker_id` | `str \| None` | ID of the worker this request was dispatched to |
| `retry_count` | `int` | Number of times this request has been retried (starts at 0) |
| `created_at` | `float` | Unix timestamp of coordinator intake |
| `dispatched_at` | `float \| None` | Unix timestamp of worker dispatch |
| `completed_at` | `float \| None` | Unix timestamp of response receipt |

**State transitions**:
```
pending → in_progress  (coordinator dispatches to worker)
in_progress → completed  (worker returns result)
in_progress → pending    (worker fails; retry_count < MAX_RETRIES)
in_progress → failed     (worker fails; retry_count == MAX_RETRIES)
```

---

### Response

Returned to the caller via the gateway after successful processing.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | `str` | Matches `Request.id` |
| `answer` | `str` | LLM-generated answer text |
| `latency_ms` | `float` | Total end-to-end time from coordinator intake to response |
| `worker_id` | `str` | ID of the worker that generated the answer |
| `retry_count` | `int` | How many retries were needed (0 = first attempt succeeded) |

---

### WorkerMetrics

Payload pushed by a worker to the coordinator at each telemetry interval.

| Field | Type | Description |
|-------|------|-------------|
| `worker_id` | `str` | Stable identifier assigned by the coordinator during registration |
| `active_tasks` | `int` | Number of requests currently being processed |
| `resource_utilization` | `float` | Current load as a fraction 0.0–1.0 (CPU or GPU, worker reports whichever is the bottleneck) |
| `avg_latency_ms` | `float` | Rolling average response latency over the last N completed requests |
| `completed_tasks` | `int` | Cumulative completed task count since worker startup |
| `failed_tasks` | `int` | Cumulative failed task count since worker startup |
| `gpu_utilization` | `float` | GPU utilization as a fraction 0.0–1.0 |
| `vram_used_gb` | `float` | Used GPU memory in GB |
| `vram_total_gb` | `float` | Total GPU memory in GB |
| `gpu_temperature_c` | `float` | GPU temperature in Celsius |
| `timestamp` | `float` | Unix timestamp when this snapshot was collected |

---

### WorkerInfo

Coordinator's live record of a registered worker.

| Field | Type | Description |
|-------|------|-------------|
| `worker_id` | `str` | Matches `WorkerMetrics.worker_id` |
| `address` | `str` | `http://host:port` — coordinator uses this to dispatch tasks |
| `name` | `str \| None` | Human-friendly worker name supplied by the worker |
| `model` | `str \| None` | Ollama model the worker is configured to serve |
| `status` | `WorkerStatus` | `healthy` \| `unavailable` |
| `last_metrics` | `WorkerMetrics \| None` | Most recent telemetry snapshot received |
| `last_heartbeat` | `float \| None` | Unix timestamp of last received heartbeat |
| `last_metrics_at` | `float \| None` | Unix timestamp of last received metrics push |
| `assigned_tasks` | `int` | Coordinator-side count of tasks dispatched to this worker and not finished yet |
| `registered_at` | `float` | Unix timestamp of initial registration |

**Status transitions**:
```
(unregistered) → healthy     (POST /register received)
healthy → unavailable        (last_heartbeat age > WORKER_STALENESS_SECONDS)
unavailable → healthy        (fresh heartbeat received)
```

---

### InferRequest

Payload the coordinator sends to a worker's `/infer` endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | `str` | Passed through for log correlation |
| `prompt` | `str` | Augmented prompt = user query + RAG context combined |
| `model` | `str` | Model name (read from `LLM_MODEL` env var on coordinator; passed explicitly so workers don't need to know) |

---

### InferResponse

Response the worker returns to the coordinator's `/infer` dispatch.

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | `str` | Echo of the incoming `request_id` |
| `answer` | `str` | LLM-generated text |
| `latency_ms` | `float` | Time from worker receiving the request to returning the answer |

---

### KnowledgeChunk

A chunk loaded into the Chroma collection at coordinator startup.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Stable chunk identifier derived from source file, chunk index, and content hash |
| `document` | `str` | Chunk text stored in Chroma |
| `embedding` | `list[float]` | Sentence-transformer embedding vector stored in Chroma |
| `source` | `str` | Source filename |
| `chunk_index` | `int` | Chunk position within the source document |

---

## Enumerations

```python
class RequestStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"

class WorkerStatus(str, Enum):
    healthy = "healthy"
    unavailable = "unavailable"
```

---

## In-Memory State (Coordinator)

| Store | Type | Contents |
|-------|------|----------|
| `worker_registry` | `dict[str, WorkerInfo]` | All known workers, keyed by `worker_id` |
| `active_requests` | `dict[str, Request]` | In-flight requests, keyed by `request_id` |
| `job_queue` | `asyncio.Queue` | Backpressure queue of requests waiting for scheduler dispatch |
| `chroma_collection` | Chroma collection handle | Remote collection used for upsert and semantic search |

---

## Configuration (Environment Variables)

All configurable values with their names and types. Full documentation is in `.env.example`.

### Control Container

| Variable | Type | Purpose |
|----------|------|---------|
| `COORDINATOR_PORT` | int | Port uvicorn listens on (NGINX proxies to this) |
| `LB_STRATEGY` | str | `least_tasks` \| `lowest_resource` \| `fastest_response` \| `gpu_aware` |
| `WORKER_STALENESS_SECONDS` | float | Max age of a heartbeat before worker is marked unavailable |
| `MAX_RETRIES` | int | Max retry attempts per request before marking failed |
| `MAX_QUEUE_SIZE` | int | Max queued requests before returning HTTP 429 |
| `REQUEST_TIMEOUT_SECONDS` | float | Timeout for coordinator → worker HTTP call |
| `LLM_MODEL` | str | Ollama model name to request from workers |
| `RAG_TOP_K` | int | Number of context passages to retrieve per query |
| `KNOWLEDGE_DIR` | str | Path inside container to knowledge base documents |
| `EMBEDDING_MODEL` | str | Sentence-transformer model name for RAG embeddings |
| `CHROMA_HOST` | str | Chroma service hostname, usually `chroma` in Docker Compose |
| `CHROMA_PORT` | int | Chroma service port, usually `8000` inside Docker Compose |
| `CHROMA_COLLECTION` | str | Chroma collection name for project knowledge |
| `NGINX_WORKER_CONNECTIONS` | int | NGINX `worker_connections` value |

### Worker Container

| Variable | Type | Purpose |
|----------|------|---------|
| `WORKER_NAME` | str | Human-friendly worker name; coordinator assigns the real `worker_id` |
| `WORKER_PORT` | int | Port this worker's FastAPI app listens on |
| `WORKER_HOST` | str | Hostname/IP the coordinator can reach this worker at |
| `COORDINATOR_URL` | str | Full base URL of coordinator, e.g. `http://192.168.1.10:8000` |
| `OLLAMA_BASE_URL` | str | Base URL of Ollama on the GPU host, e.g. `http://localhost:11434` |
| `HEARTBEAT_INTERVAL_SECONDS` | float | How often to send lightweight liveness heartbeats |
| `METRICS_INTERVAL_SECONDS` | float | How often to push metrics to coordinator |
| `REGISTRATION_RETRY_SECONDS` | float | Retry interval when coordinator is unreachable at startup |
| `LATENCY_WINDOW` | int | Number of recent completions to include in avg latency calculation |
