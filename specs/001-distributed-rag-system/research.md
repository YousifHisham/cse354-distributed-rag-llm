# Research: Distributed RAG-Augmented LLM System

**Phase**: 0 | **Date**: 2026-05-11

---

## Decision 1: Inter-Service Communication Protocol

**Decision**: HTTP/REST with JSON payloads over plain TCP between all services.

**Rationale**: All three communication paths (gateway → coordinator, coordinator → worker, worker → coordinator metrics push) are request/response or fire-and-push patterns that map naturally to HTTP POST. HTTP is natively supported by FastAPI, compatible with NGINX proxying, and requires no additional broker or binary protocol library. Async HTTP (via `httpx.AsyncClient`) lets the coordinator dispatch to multiple workers concurrently without threads.

**Alternatives considered**:
- gRPC: adds protobuf schema complexity and a binary protocol that is harder to inspect; not justified for this scale.
- Message queue (Redis/RabbitMQ): adds a third infrastructure component, violates the two-container constraint.
- WebSocket: useful for streaming responses but adds bidirectional state complexity not needed here.

---

## Decision 2: Worker Heartbeats and Metrics

**Decision**: Workers send lightweight heartbeats to the coordinator at `HEARTBEAT_INTERVAL_SECONDS` and push fuller load-balancing metrics at `METRICS_INTERVAL_SECONDS`.

**Rationale**: Heartbeats and metrics have different jobs. Heartbeats are small and frequent, so they are the best signal for worker liveness and failure detection. Metrics can be less frequent because they carry heavier telemetry used for load-balancing decisions. The coordinator timestamps each heartbeat and marks a worker unavailable when the heartbeat age exceeds `WORKER_STALENESS_SECONDS`.

**Alternatives considered**:
- Pull (coordinator polls workers): requires coordinator to maintain a polling schedule per worker; adds latency to failure detection equal to the poll interval.
- Metrics-only heartbeat: simpler, but either sends metrics too often or detects failures too slowly.
- Heartbeat-only (no metrics): would require a separate mechanism to collect load data for routing decisions.

---

## Decision 3: RAG Vector Store

**Decision**: ChromaDB server with sentence-transformers for embedding generation. Chroma runs as a separate vector database container, while the coordinator embeds local knowledge documents and upserts/query vectors through Chroma's HTTP API.

**Rationale**: Chroma makes the RAG database an explicit distributed-system component instead of hidden in coordinator memory. It gives the project a clearer separation between orchestration and storage, persists vectors across restarts, and can be inspected independently during demos. The coordinator still owns document ingestion and prompt enrichment, but retrieval state lives in a separate service.

**Alternatives considered**:
- FAISS: fast and simple, but keeps the index in coordinator memory and makes the vector store less visible as an independent component.
- Pinecone/Weaviate: cloud-hosted, introduces external dependency and network latency on every query.
- TF-IDF (BM25): purely keyword-based, misses semantic similarity. Chroma + sentence-transformers gives semantic retrieval with a real vector database.

---

## Decision 4: LLM Inference on Worker

**Decision**: Workers call Ollama via HTTP on `OLLAMA_BASE_URL` (default `http://localhost:11434`). Ollama runs as a separate process on the GPU host, outside the worker container, accessible via host networking.

**Rationale**: GPU passthrough inside Docker containers adds complexity (requires `--gpus all` flag and NVIDIA Container Toolkit). Ollama running natively on the GPU host has direct, unrestricted GPU access. The worker container connects to Ollama over the loopback interface (or a host-network address supplied via `OLLAMA_BASE_URL` env var). This pattern is standard for GPU inference workloads on cloud VMs.

**Alternatives considered**:
- Bundle Ollama inside the worker container: possible with `--gpus all` but requires NVIDIA Container Toolkit on every GPU host and complicates the Dockerfile significantly.
- vLLM: high-performance inference server but harder to set up than Ollama; Ollama is sufficient for this project scope.
- Direct Hugging Face transformers: no server abstraction, requires managing model loading and CUDA in Python directly.

---

## Decision 5: Multi-Process Management in Control Container

**Decision**: Use Supervisord to run NGINX and Uvicorn (the FastAPI coordinator) as two supervised processes inside the control container.

**Rationale**: The control container must run both NGINX (gateway) and the Python coordinator, but Docker containers natively run a single foreground process. Supervisord is the standard lightweight solution for this: it starts both processes, restarts them on crash, and its own process becomes the container's PID 1. The alternative of using a shell script as entrypoint is fragile (signals not forwarded correctly, no restart on crash).

**Alternatives considered**:
- Two containers for NGINX and coordinator: violates the two-container constraint.
- s6-overlay: more complex init system; Supervisord is simpler and sufficient.
- Run coordinator behind NGINX using uWSGI: uWSGI is heavier than Uvicorn + direct proxy; Uvicorn's ASGI performance is better for async FastAPI.

---

## Decision 6: Load Balancing Strategy Selection

**Decision**: The active strategy is selected by the `LB_STRATEGY` environment variable. The coordinator reads this at startup and instantiates only the corresponding strategy class. Three strategies, all operating on the live `WorkerMetrics` snapshot:

| Strategy value | Routing criterion | Metric used |
|---|---|---|
| `least_tasks` | Fewest active tasks right now | `active_tasks` in WorkerMetrics |
| `lowest_resource` | Lowest resource utilization | `resource_utilization` (0.0–1.0) in WorkerMetrics |
| `fastest_response` | Best recent average latency | `avg_latency_ms` in WorkerMetrics |
| `gpu_aware` | Best combined GPU score | active tasks, GPU utilization, VRAM ratio, and latency |

For all strategies: workers with stale heartbeats (age > `WORKER_STALENESS_SECONDS`) are excluded before selection.

**Rationale**: Separating strategies into pluggable classes means changing strategy requires only an env var change and coordinator restart — no worker restart needed. All three criteria use data the worker already reports; no additional instrumentation required per strategy.

---

## Decision 7: Worker Registration Flow

**Decision**: On startup, the worker sends a POST to `COORDINATOR_URL/register` with its own address (`WORKER_HOST:WORKER_PORT`). It retries at `REGISTRATION_RETRY_SECONDS` intervals until the coordinator responds with 200. Once registered, it begins heartbeat and metrics push loops immediately.

**Rationale**: Workers are started independently and may come online before or after the coordinator. Retry-until-success decouples startup order. The coordinator adds the worker to its registry on first successful registration and keeps it active while fresh heartbeats arrive.

---

## Decision 8: Coordinator Request Queue

**Decision**: The coordinator uses an `asyncio.Queue` as a bounded request queue. Incoming requests are enriched with RAG context, placed into the queue, and then a background scheduler loop pulls jobs and starts concurrent async dispatch tasks.

**Rationale**: A queue gives the coordinator explicit backpressure under high concurrency. If the queue reaches `MAX_QUEUE_SIZE`, the coordinator returns HTTP 429 instead of accepting unlimited work. The scheduler pulls one job at a time, but each worker call is launched as its own async task, so LLM requests still run concurrently.
