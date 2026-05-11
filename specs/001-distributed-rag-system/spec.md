# Feature Specification: Distributed RAG-Augmented LLM System with Load Balancing and Fault Tolerance

**Feature Branch**: `001-distributed-rag-system`  
**Created**: 2026-05-11  
**Status**: Draft  
**Course**: CSE354 Distributed Computing — 2nd Semester 2025/2026, Ain Shams University

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — End-to-End AI Query Processing (Priority: P1)

A user submits a natural-language question to the AI assistant. The request arrives at the HTTP gateway, which forwards it to the master coordinator. The coordinator assigns a unique ID, retrieves relevant context from the knowledge base, combines it with the user's question, and dispatches the augmented prompt to an available GPU worker. The worker generates the AI response and returns it through the coordinator back to the user.

**Why this priority**: This is the complete request flow. Without it working end-to-end, no other capability can be demonstrated.

**Independent Test**: Send a single question to the gateway and receive a coherent, knowledge-augmented AI answer with a unique request ID in the response.

**Acceptance Scenarios**:

1. **Given** the system is running with at least one GPU worker registered, **When** a user submits a question to the gateway, **Then** the coordinator returns an AI-generated answer that incorporates context retrieved from the knowledge base.
2. **Given** a user submits a question, **When** the coordinator processes it, **Then** the response includes the unique request ID assigned at intake.
3. **Given** the knowledge base contains no relevant documents for a query, **When** the retrieval step completes empty, **Then** the coordinator still forwards the query to a worker and returns a best-effort answer without failing.

---

### User Story 2 — Data-Driven Distribution Across GPU Workers (Priority: P2)

When multiple requests arrive simultaneously, the coordinator selects the target worker based on real metrics that each worker continuously reports — such as how many tasks it is currently processing, its current resource utilization, and how quickly it has been responding. The routing decision is always derived from live worker data, never from a predetermined pattern or fixed cycle.

**Why this priority**: Demonstrates the distributed value of the system. A single-worker system is not a distributed system; and predetermined cycling is not distribution, it is just rotation.

**Independent Test**: With two workers running where one is deliberately kept busier than the other, observe via coordinator logs that the less-busy worker receives a higher proportion of new requests under each of the three strategies.

**Acceptance Scenarios**:

1. **Given** two or more GPU workers are registered and reporting metrics, **When** multiple requests arrive concurrently, **Then** the coordinator selects target workers based on their reported live data — not in a fixed predetermined order.
2. **Given** the least-tasks strategy is active, **When** a new request arrives, **Then** the coordinator routes it to the worker reporting the lowest number of currently active tasks.
3. **Given** the resource-utilization strategy is active, **When** a new request arrives, **Then** the coordinator routes it to the worker reporting the lowest current resource load.
4. **Given** the fastest-response strategy is active, **When** a new request arrives, **Then** the coordinator routes it to the worker with the best recent average response latency as reported in worker metrics.
5. **Given** one worker becomes more loaded at runtime, **When** new requests arrive, **Then** the coordinator's routing shifts toward the less-loaded worker automatically — no configuration change required.
6. **Given** the active strategy is changed in the environment configuration, **When** the coordinator is restarted, **Then** it applies the new strategy without any change to the gateway or worker containers.

---

### User Story 3 — Automatic Fault Detection and Task Recovery (Priority: P3)

If a GPU worker becomes unresponsive or fails while processing a request, the coordinator detects the failure, removes the node from active routing, and retries the incomplete task on another healthy worker. The user eventually receives an answer without being aware of the failure.

**Why this priority**: Fault tolerance is an explicit graded requirement of the course and a core property of production distributed systems.

**Independent Test**: With two workers running, shut down one worker mid-processing; verify that requests assigned to the failed worker are retried on the surviving worker and answered.

**Acceptance Scenarios**:

1. **Given** a worker stops responding, **When** the coordinator's health check finds it unresponsive, **Then** the worker is marked unavailable within the configured detection window and no further requests are routed to it.
2. **Given** a worker fails while processing a request, **When** the coordinator detects the failure, **Then** the in-flight request is retried on a different healthy worker.
3. **Given** only one worker remains healthy after a failure, **When** new requests arrive, **Then** the coordinator continues serving them using the surviving worker.
4. **Given** a previously failed worker restarts and passes health checks, **When** the coordinator's next health check cycle runs, **Then** the worker is restored to the active pool and begins receiving requests again.

---

### Edge Cases

- What happens when all registered workers are unavailable simultaneously?
- What happens when a request exceeds the maximum retry count across multiple workers?
- What happens when the knowledge base returns no results for a query?
- What happens when a worker returns an error response instead of going silent?
- What happens when the coordinator receives more simultaneous requests than all workers combined can queue?

---

## Requirements *(mandatory)*

### Functional Requirements

**Request Handling**

- **FR-001**: The system MUST expose a single public HTTP entry point (the gateway) that accepts user questions and returns AI-generated answers.
- **FR-002**: The gateway MUST forward every incoming request to the master coordinator; the gateway performs no inference, no RAG retrieval, and no load balancing itself.
- **FR-003**: The coordinator MUST assign each incoming request a unique identifier before any further processing.
- **FR-004**: The coordinator MUST retrieve relevant context from the knowledge base and combine it with the original user query before dispatching to a worker.

**Configuration**

- **FR-005**: Every configurable value in the system MUST be supplied via environment variables — including but not limited to: coordinator address, worker addresses, active load-balancing strategy, health-check interval, request timeout, maximum retry count, knowledge base path, and LLM model name.
- **FR-006**: No IP addresses, port numbers, hostnames, model names, file paths, or numeric thresholds MAY be hardcoded in any source file or configuration template.

**Worker Telemetry**

- **FR-007**: Each worker MUST continuously push a metrics payload to the coordinator at a configurable interval; the payload MUST include at minimum: current active task count, current resource utilization, and recent average response latency.
- **FR-008**: The coordinator MUST store the most recent metrics snapshot for each registered worker and use only that live data when making routing decisions.

**Load Balancing**

- **FR-009**: The coordinator MUST support three selectable data-driven request-distribution strategies, all based on live worker metrics:
  - **Least-tasks**: route to the worker currently reporting the fewest active tasks.
  - **Lowest-resource**: route to the worker currently reporting the lowest resource utilization.
  - **Fastest-response**: route to the worker currently reporting the best recent average response latency.
- **FR-010**: The active strategy MUST be selected solely by an environment variable; changing the strategy MUST require only a coordinator restart, not a code change or worker restart.
- **FR-011**: The coordinator MUST only dispatch requests to workers whose most recent metrics snapshot is within the configured staleness window; a worker whose metrics have not been received within that window MUST be treated as unavailable.

**Worker Management**

- **FR-012**: Each GPU worker MUST independently receive a task from the coordinator, run LLM inference, and return the result to the coordinator.
- **FR-013**: The coordinator MUST maintain a registry of all known workers including their address, current status (healthy/unavailable), and most recent metrics snapshot.
- **FR-014**: Workers MUST register themselves with the coordinator on startup using their address supplied via environment variable.

**Fault Tolerance**

- **FR-015**: A worker that stops sending metrics within the configured staleness window MUST be marked unavailable by the coordinator (metrics staleness doubles as the health signal — no separate health-check ping is required).
- **FR-016**: The coordinator MUST not route new requests to unavailable workers.
- **FR-017**: Any request assigned to a worker that becomes unavailable before returning a result MUST be retried on another healthy worker, up to the configured maximum retry count.
- **FR-018**: When a worker resumes sending metrics and its snapshot is fresh, the coordinator MUST automatically return it to the active pool.

**Observability**

- **FR-019**: The coordinator MUST log every request lifecycle event: received, enriched, dispatched (including which worker and why it was chosen), succeeded or failed, and retried.
- **FR-020**: The coordinator MUST log every worker state change: registered, marked unavailable (with last-seen timestamp), restored to active.

**Deployment**

- **FR-021**: The entire system MUST be packaged as exactly two Docker container images:
  - **Control container**: runs the HTTP gateway, the master coordinator, and the RAG module together; deployed on the local MacBook.
  - **Worker container**: runs the LLM inference service and the metrics reporter; one instance deployed per GPU node.
- **FR-022**: All environment variables for both containers MUST be documented in a `.env.example` file at the project root.
- **FR-023**: The worker container MUST start and operate independently of the control container; if the coordinator is not yet reachable on startup, the worker MUST retry registration at a configurable interval until it succeeds.

### Key Entities

- **HTTPGateway**: Public entry point that accepts concurrent user connections and proxies each to the coordinator; owns no business logic.
- **MasterCoordinator**: Orchestrates the full request lifecycle — RAG enrichment, worker selection via active load-balancing strategy, in-flight task tracking, retry logic, health monitoring.
- **GPUWorker**: A registered compute node that runs LLM inference and continuously pushes a live metrics payload to the coordinator.
- **WorkerMetrics**: A snapshot reported by a worker at each telemetry interval — includes active task count, resource utilization, and recent average response latency. The coordinator uses only the latest snapshot for routing decisions.
- **Request**: Unique ID, original query text, retrieved context, assigned worker ID, status (pending / in-progress / completed / failed), retry count, timestamps.
- **Response**: Request ID, generated answer text, total end-to-end latency, worker ID that processed it.
- **KnowledgeBase**: Indexed document store; accepts a query and returns ranked context passages.
- **WorkerRegistry**: The coordinator's live record of all known workers, their addresses, current status, and most recent WorkerMetrics snapshot.

---

## Success Criteria *(mandatory)*

- **SC-001**: A user question submitted to the gateway receives a coherent AI-generated answer that demonstrably incorporates knowledge-base context.
- **SC-002**: With two or more workers running where one is deliberately kept busier, coordinator logs show that routing decisions shift toward the less-loaded worker — confirming decisions are driven by live worker data, not a fixed pattern.
- **SC-003**: Shutting down one worker while requests are in-flight results in those requests being retried and answered by the remaining worker, with no permanent user-visible failure.
- **SC-004**: The coordinator stops routing to a failed worker within the staleness window configured via environment variable.
- **SC-005**: All three data-driven strategies are switchable via environment variable alone; each produces observably different routing outcomes in coordinator logs under the same workload conditions.
- **SC-006**: The entire system starts from two Docker images configured entirely by environment variables; no value in any source file or Dockerfile needs editing to deploy to a different environment.
- **SC-007**: The coordinator logs are sufficient to trace any single request from gateway receipt through RAG enrichment, worker dispatch, and final response (or failure and retry chain).

---

## Assumptions

- The HTTP gateway and master coordinator run in the same control container on a MacBook; they communicate over localhost inside that container.
- The RAG module also runs inside the control container; it is not a separate network service.
- At least one GPU worker (worker container) is reachable from the MacBook over the network.
- The knowledge base is pre-loaded into the control container at build or startup time; dynamic ingestion of new documents is out of scope.
- The maximum retry count per request is bounded by environment variable to prevent infinite loops; a request that exhausts all retries is logged as permanently failed.
- No automated test files, test suites, or load-testing scripts are part of the deliverable; correctness is demonstrated by running the live system.
- A polished user-facing UI is out of scope; the entry point is an HTTP endpoint (e.g., accepts JSON).
