# Tasks: Distributed RAG-Augmented LLM System

**Input**: Design documents from `specs/001-distributed-rag-system/`
**Prerequisites**: plan.md ✓ spec.md ✓ research.md ✓ data-model.md ✓ contracts/ ✓

**No test tasks** — excluded per spec (no test files or test infrastructure in scope).

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no shared state dependencies)
- **[Story]**: User story this task delivers ([US1], [US2], [US3])

---

## Phase 1: Setup

**Purpose**: Create project skeleton, dependency manifests, and environment configuration.

- [x] T001 Create full directory structure: `control/coordinator/`, `control/nginx/`, `control/knowledge/`, `worker/` at repo root
- [x] T002 [P] Create `control/requirements.txt` — fastapi, uvicorn[standard], httpx, chromadb, sentence-transformers, pydantic, python-dotenv, psutil
- [x] T003 [P] Create `worker/requirements.txt` — fastapi, uvicorn[standard], httpx, pydantic, python-dotenv, psutil
- [x] T004 Create `.env.example` with every variable from `specs/001-distributed-rag-system/data-model.md` — grouped by container, each line documented with type and default value

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared Pydantic model definitions for both containers. No user story can be implemented without these.

**⚠️ CRITICAL**: Complete before any Phase 3+ work begins.

- [x] T005 [P] Create `control/coordinator/models.py` — Pydantic models: `Request`, `Response`, `WorkerInfo`, `WorkerMetrics`, `InferRequest`, `InferResponse`; enums `RequestStatus` and `WorkerStatus` exactly as defined in `specs/001-distributed-rag-system/data-model.md`
- [x] T006 [P] Create `worker/models.py` — Pydantic models: `InferRequest`, `InferResponse`, `WorkerMetrics`, `WorkerRegistration` (used in POST /register body)

**Checkpoint**: Models defined — Phase 3 can begin.

---

## Phase 3: User Story 1 — End-to-End AI Query Processing (Priority: P1) 🎯 MVP

**Goal**: A user POSTs a query to the gateway, the coordinator enriches it with RAG context and dispatches it to the first available registered worker, the worker calls Ollama and returns an answer, the user receives `Response` JSON.

**Independent Test**: With one worker running and Ollama available, `POST /query {"query": "..."}` returns a non-empty `answer` that contains text derived from the knowledge base documents; `request_id` is a UUID; `worker_id` matches the registered worker.

### Implementation

- [x] T007 [P] [US1] Implement `control/coordinator/rag.py` — `ChromaRetriever` class: load all `.txt`/`.md` files from `KNOWLEDGE_DIR` at startup, chunk documents, compute sentence-transformer embeddings (`EMBEDDING_MODEL` env var), upsert chunks into the configured Chroma collection, expose `retrieve(query: str, top_k: int) -> str` that returns concatenated top-k passage strings
- [x] T008 [P] [US1] Implement `control/coordinator/registry.py` — `WorkerRegistry` class: `dict[str, WorkerInfo]` backing store; methods: `register(worker_id, address) -> WorkerInfo`, `get_all_healthy() -> list[WorkerInfo]`, `get_first_healthy() -> WorkerInfo | None`; thread-safe via `asyncio.Lock`
- [x] T009 [P] [US1] Implement `worker/main.py` — FastAPI app with: `POST /infer` endpoint that accepts `InferRequest`, calls Ollama at `OLLAMA_BASE_URL/api/generate` with `{"model": model, "prompt": prompt, "stream": false}`, returns `InferResponse`; startup event that POSTs `WorkerRegistration` to `COORDINATOR_URL/register` and retries every `REGISTRATION_RETRY_SECONDS` until coordinator responds 200 or 409
- [x] T010 [US1] Implement `control/coordinator/main.py` — FastAPI app with: `POST /register` (adds worker to registry, returns 200 or 409), `GET /health` (returns healthy/total worker counts), `POST /query` that runs: assign UUID → `ChromaRetriever.retrieve()` → build augmented prompt → `registry.get_first_healthy()` → `httpx.AsyncClient.post(worker.address + "/infer")` with `REQUEST_TIMEOUT_SECONDS` → return `Response`; return 503 if no healthy workers
- [x] T011 [P] [US1] Create `control/nginx/nginx.conf` — `upstream coordinator { server localhost:${COORDINATOR_PORT}; }`, `server { listen 80; location / { proxy_pass http://coordinator; proxy_read_timeout ...; } }`; all numeric values templated via env var substitution using `envsubst` at container startup
- [x] T012 [P] [US1] Create `control/supervisord.conf` — `[program:nginx]` running `nginx -g 'daemon off;'` and `[program:uvicorn]` running `uvicorn coordinator.main:app --host 0.0.0.0 --port %(ENV_COORDINATOR_PORT)s`; both `autorestart=true`
- [x] T013 [P] [US1] Create `control/Dockerfile` — `FROM python:3.11-slim`, install `nginx` + `supervisor` via apt, copy `requirements.txt` and `pip install`, copy `coordinator/`, `knowledge/`, `nginx/nginx.conf`, `supervisord.conf`; `ENTRYPOINT ["supervisord", "-c", "/etc/supervisord.conf"]`; all runtime config via env vars
- [x] T014 [P] [US1] Create `worker/Dockerfile` — `FROM python:3.11-slim`, copy `requirements.txt` and `pip install`, copy `worker/`; `CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]` (port overrideable via env var in compose)
- [x] T015 [US1] Add 3 sample knowledge documents to `control/knowledge/` (plain `.txt` files, ~200 words each, topic of your choice) — used to verify RAG context is appended to prompts

**Checkpoint**: US1 complete. `docker build` both images, start control + one worker, POST a query, receive a RAG-augmented answer. ✓

---

## Phase 4: User Story 2 — Data-Driven Load Balancing (Priority: P2)

**Goal**: Workers push lightweight heartbeats every `HEARTBEAT_INTERVAL_SECONDS` and live metrics (`active_tasks`, `resource_utilization`, `avg_latency_ms`) every `METRICS_INTERVAL_SECONDS`. The coordinator routes each new request using one of three strategies selected entirely via the `LB_STRATEGY` env var. Routing decisions are visibly different between strategies in coordinator logs.

**Independent Test**: Run two workers; keep one busy with a long-running request; observe in coordinator logs that subsequent requests route to the less-busy worker. Change `LB_STRATEGY`, restart only the coordinator container, observe different routing behaviour.

### Implementation

- [x] T016 [P] [US2] Extend `worker/main.py` with metrics state — module-level: `active_tasks: int = 0`, `completed_tasks: int = 0`, `failed_tasks: int = 0`, `latency_window: collections.deque` (maxlen=`LATENCY_WINDOW`); increment/decrement `active_tasks` around each `/infer` call using `try/finally`; append latency to `latency_window` on completion; compute `avg_latency_ms` as mean of window; compute `resource_utilization` via `psutil.cpu_percent()/100`
- [x] T017 [US2] Add heartbeat and metrics background tasks to `worker/main.py` — every `HEARTBEAT_INTERVAL_SECONDS` POST `WorkerHeartbeat` to `COORDINATOR_URL/heartbeat`; every `METRICS_INTERVAL_SECONDS` POST `WorkerMetrics` to `COORDINATOR_URL/metrics`; on 404 response re-trigger registration flow
- [x] T018 [P] [US2] Extend `control/coordinator/registry.py` — add `update_heartbeat()` and `update_metrics()` methods that store `last_heartbeat`, `last_metrics`, and `last_metrics_at`; use heartbeat freshness for worker availability
- [x] T019 [US2] Add `POST /metrics` endpoint to `control/coordinator/main.py` — validates `worker_id` exists in registry; calls `registry.update_metrics()`; returns `{"status": "received"}` or 404 if unknown worker
- [x] T020 [P] [US2] Create `control/coordinator/balancer.py` — abstract base `Strategy` with `select(workers: list[WorkerInfo]) -> WorkerInfo`; three concrete classes: `LeastTasksStrategy` (min `last_metrics.active_tasks`), `LowestResourceStrategy` (min `last_metrics.resource_utilization`), `FastestResponseStrategy` (min `last_metrics.avg_latency_ms`, treating 0.0 as best); factory `get_strategy(name: str) -> Strategy` reads `LB_STRATEGY` env var
- [x] T021 [US2] Wire balancer into coordinator `/query` handler in `control/coordinator/main.py` — replace `registry.get_first_healthy()` with `balancer.select(registry.get_all_healthy())`; log which worker was selected and why (which metric value caused selection); instantiate strategy once at app startup from `LB_STRATEGY` env var

**Checkpoint**: US2 complete. With two workers and different loads, coordinator logs show strategy-driven routing. Change `LB_STRATEGY` + restart control-only → routing changes. ✓

---

## Phase 5: User Story 3 — Automatic Fault Detection and Task Recovery (Priority: P3)

**Goal**: When a worker stops pushing heartbeats, the coordinator marks it unavailable within `WORKER_STALENESS_SECONDS`. Any request dispatched to a worker that returns an error is retried on another healthy worker, up to `MAX_RETRIES`. When a worker resumes heartbeats, it is automatically restored.

**Independent Test**: Start two workers. Stop one (`docker stop worker-X`). Within `WORKER_STALENESS_SECONDS`, coordinator logs show `[registry] worker-X marked unavailable`. Send a request that would have gone to the stopped worker — confirm it is retried and answered by the other worker.

### Implementation

- [x] T022 [P] [US3] Add staleness-checker background task to `control/coordinator/main.py` — `asyncio` task started in `lifespan`; runs every `WORKER_STALENESS_SECONDS / 2` seconds; iterates all `WorkerInfo` in registry; marks any worker with `last_heartbeat` age > `WORKER_STALENESS_SECONDS` as `unavailable`; logs `[registry] {worker_id} marked unavailable (last heartbeat {age:.1f}s ago)`
- [x] T023 [P] [US3] Add auto-recovery in `control/coordinator/registry.py` — in `update_heartbeat()`, if the worker's current status is `unavailable`, set it back to `healthy` and log `[registry] {worker_id} restored to healthy`
- [x] T024 [US3] Add retry loop to `/query` handler in `control/coordinator/main.py` — wrap worker dispatch in a `for attempt in range(MAX_RETRIES + 1)` loop; on `httpx.TimeoutException`, `httpx.ConnectError`, or non-200 worker response: log `[query] {request_id} retry {attempt}/{MAX_RETRIES} — worker {worker_id} failed`; re-select a different healthy worker (exclude the failed one for this request); after exhausting retries return HTTP 504 with `{"detail": "Request failed after {n} retries"}`; return 503 immediately if no healthy workers at selection time
- [x] T025 [P] [US3] Add structured lifecycle logging throughout `control/coordinator/main.py` — log at each stage: `[query] {id} received`, `[query] {id} enriched (context {n} chars)`, `[query] {id} dispatched to {worker_id} via {strategy} (metric={value:.2f})`, `[query] {id} completed in {latency:.0f}ms (retries={n})`, `[query] {id} failed permanently`

**Checkpoint**: US3 complete. Stopping a worker mid-flight produces retry logs and the user still gets an answer. Coordinator health endpoint shows updated healthy_workers count. ✓

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T026 [P] Create `docker-compose.yml` — `chroma` service using the official Chroma image with a persistent volume; `control` service: `build: ./control`, `ports: ["80:80"]`, `env_file: .env`, depends on `chroma`; `worker` service: `build: ./worker`, `network_mode: "host"`, `env_file: .env`; all services use `restart: unless-stopped`
- [x] T027 [P] Complete `.env.example` — ensure every variable from `specs/001-distributed-rag-system/data-model.md` is present with a sensible default and a one-line comment explaining its purpose and valid values
- [x] T028 Add graceful shutdown to `control/coordinator/main.py` — on SIGTERM, stop accepting new requests, wait for all in-flight `active_requests` to complete or time out before exiting
- [x] T029 Add graceful shutdown to `worker/main.py` — on SIGTERM, stop heartbeat and metrics push loops, wait for current `/infer` call to complete before exiting
- [x] T030 [P] Run quickstart.md validation — build both images, start system with `docker compose up`, execute every `curl` command from `specs/001-distributed-rag-system/quickstart.md`, verify expected responses

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — blocks all user story phases
- **Phase 3 (US1)**: Depends on Phase 2 — delivers MVP
- **Phase 4 (US2)**: Depends on Phase 3 (extends coordinator and worker built in US1)
- **Phase 5 (US3)**: Depends on Phase 4 (retry logic needs the balancer to re-select workers)
- **Phase 6 (Polish)**: Depends on Phases 3–5

### User Story Dependencies

| Story | Depends on | Can start after |
|-------|-----------|-----------------|
| US1 (P1) | Phase 2 complete | Models defined |
| US2 (P2) | US1 complete | Worker + coordinator running |
| US3 (P3) | US2 complete | Metrics infrastructure in place |

### Within Each Phase — Parallel Opportunities

**Phase 3 (US1)**: T007, T008, T009 can run in parallel (separate files: rag.py, registry.py, worker/main.py). T010 depends on T007 + T008. T011, T012, T013, T014 can run in parallel after T010 starts.

**Phase 4 (US2)**: T016, T018, T020 can run in parallel (separate files). T017 depends on T016. T019 depends on T018. T021 depends on T020.

**Phase 5 (US3)**: T022, T023, T025 can run in parallel. T024 depends on T020 (balancer, for re-selection).

---

## Parallel Execution Examples

### Phase 3 — US1 (10 tasks, ~3 parallel streams)

```
Stream A: T007 (rag.py) → done
Stream B: T008 (registry.py) + T009 (worker/main.py) → T010 (coordinator/main.py)
Stream C: T011 (nginx.conf) + T012 (supervisord.conf) + T013 (control Dockerfile) + T014 (worker Dockerfile)
T015: knowledge documents (any time during Phase 3)
```

### Phase 4 — US2 (6 tasks, ~3 parallel streams)

```
Stream A: T016 (worker metrics state) → T017 (metrics push loop)
Stream B: T018 (registry update_metrics) → T019 (POST /metrics endpoint)
Stream C: T020 (balancer.py) → T021 (wire into /query)
```

---

## Implementation Strategy

### MVP (User Story 1 only — Phases 1–3)

1. Phase 1: Setup
2. Phase 2: Foundational models
3. Phase 3: US1 end-to-end flow
4. **STOP and VALIDATE**: `POST /query` returns a RAG-augmented answer via Dockerised system
5. Demo-ready at this point

### Full System (Sequential delivery)

1. MVP above
2. Phase 4 (US2): Add live-data load balancing → validate routing shifts with load
3. Phase 5 (US3): Add fault detection + retry → validate node-failure recovery
4. Phase 6: Polish + docker-compose + graceful shutdown

---

## Notes

- No test files — correctness verified by running the live system per `quickstart.md`
- All [P] tasks write to different files; they have no shared-state write conflicts
- Commit after each phase checkpoint
- `WORKER_NAME` is only a human-friendly label. The coordinator assigns unique worker IDs during registration.
- Ollama must be running on the GPU host before starting the worker container
