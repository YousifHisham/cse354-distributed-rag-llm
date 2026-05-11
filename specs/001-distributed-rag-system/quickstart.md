# Quickstart: Distributed RAG-Augmented LLM System

## Prerequisites

- Docker installed on the MacBook (control machine)
- Docker installed on each GPU cloud node (worker machines)
- Ollama installed and running natively on each GPU node (`ollama serve`)
- The desired model pulled on each GPU node: `ollama pull llama3.2:8b` (or whichever model you set in `.env`)
- Both machines must be able to reach each other over the network

---

## 1. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Key values to set:

| Variable | What to put |
|----------|-------------|
| `LB_STRATEGY` | `least_tasks`, `lowest_resource`, `fastest_response`, or `gpu_aware` |
| `LLM_MODEL` | Model name pulled in Ollama, e.g. `llama3.2:8b` |
| `COORDINATOR_PORT` | Port the coordinator listens on inside the control container (e.g. `8000`) |
| `MAX_QUEUE_SIZE` | Maximum queued requests before the coordinator returns HTTP 429 |

You do not need to edit any source file. All behaviour is controlled by `.env`.

---

## 2. Start Chroma + the Control Container (MacBook)

```bash
docker compose up -d --build chroma control
```

The Chroma vector database is now running as a separate container. It is exposed on `http://localhost:8002` for local inspection, while the coordinator reaches it internally at `http://chroma:8000`.

The NGINX gateway is listening on `http://localhost:80`. The coordinator is running inside the control container on `localhost:$COORDINATOR_PORT`, reachable only via NGINX from outside.

---

## 3. Start a Worker Container (GPU Node)

### One-command GPU node setup

On your MacBook, expose the control plane through ngrok after starting `chroma` and `control`:

```bash
ngrok http 80
```

Copy the generated public URL, for example:

```text
https://abc123.ngrok-free.app
```

On each GPU cloud node, copy the project folder, then run from the project root:

```bash
./scripts/bootstrap_gpu_node.sh https://abc123.ngrok-free.app gpu-worker-1 llama3.2:8b
```

This script installs Ollama if needed, starts Ollama, pulls the selected model, builds the worker Docker image, creates `.gpu-worker.env`, and starts the worker container using host networking.

For the second GPU node, use a different worker name:

```bash
./scripts/bootstrap_gpu_node.sh https://abc123.ngrok-free.app gpu-worker-2 llama3.2:8b
```

If the script cannot detect the worker's public IP, pass it explicitly:

```bash
WORKER_HOST=<gpu-node-public-ip> ./scripts/bootstrap_gpu_node.sh https://abc123.ngrok-free.app gpu-worker-1 llama3.2:8b
```

The coordinator assigns the real `worker_id` during registration. The worker name is only a human-friendly label.

### Manual worker setup

On each GPU cloud node, copy the `worker/` directory and your `.env` file, then:

```bash
docker build -t distributed-worker ./worker
docker run -d \
  --env-file .env \
  --network host \
  --name worker-1 \
  distributed-worker
```

`--network host` lets the worker container reach the Ollama process running on the GPU host at `localhost:11434`. Set `WORKER_HOST` in `.env` to the GPU node's external IP so the coordinator can dispatch tasks back to it.

Repeat for each additional GPU node, changing `WORKER_NAME` in the env for each instance. The coordinator will assign unique worker IDs automatically.

---

## 4. Verify the System

Check that workers registered successfully by querying the coordinator's health endpoint through the gateway:

```bash
curl http://localhost/health
```

Expected response:
```json
{"status": "ok", "healthy_workers": 2, "total_workers": 2}
```

Inspect heartbeat and metrics state:

```bash
curl http://localhost/workers
```

Each worker should show a recent `last_heartbeat` value and, after a few seconds, `last_metrics`.

---

## 5. Send a Query

```bash
curl -X POST http://localhost/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What is distributed computing?"}'
```

Expected response:
```json
{
  "request_id": "...",
  "answer": "...",
  "latency_ms": 1234.5,
  "worker_id": "worker-1",
  "retry_count": 0
}
```

---

## 6. Switch Load Balancing Strategy

Switch strategies live without restarting:

```bash
curl -X POST http://localhost/config/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "gpu_aware"}'
```

Check the active strategy:

```bash
curl http://localhost/config/strategy
```

Valid strategies are `least_tasks`, `lowest_resource`, `fastest_response`, and `gpu_aware`.

---

## 7. Run Load Tests

Run a debuggable single load test with per-request output files:

```bash
python3 scripts/load_generator.py \
  --url http://localhost \
  --strategy least_tasks \
  --users 100 \
  --requests 100 \
  --queries-file scripts/queries.txt \
  --out-dir results/manual \
  --label least_tasks_100 \
  --verbose
```

This writes:

```text
results/manual/least_tasks_100_summary.json
results/manual/least_tasks_100_requests.jsonl
results/manual/least_tasks_100_errors.jsonl
```

Run a full load suite for one strategy:

```bash
STRATEGY=gpu_aware \
LEVELS="100 250 500 1000" \
QUERIES_FILE=scripts/queries.txt \
./scripts/run_load_suite.sh
```

Compare all strategies at the same concurrency:

```bash
USERS=500 \
REQUESTS=500 \
QUERIES_FILE=scripts/queries.txt \
./scripts/run_strategy_comparison.sh
```

Run the PDF-oriented evaluation:

```bash
QUERIES_FILE=scripts/queries.txt ./scripts/run_pdf_evaluation.sh
```

Each wrapper creates a timestamped folder under `results/` with `run.log`, before/after health snapshots, worker state, debug state, Prometheus snapshots, raw per-request JSONL, error JSONL, summary JSON files, and `summary.md`.

---

## 8. Test Fault Tolerance

While the system is running with two or more workers, stop one worker:

```bash
# On the GPU node
docker stop worker-1
```

Within `WORKER_STALENESS_SECONDS`, the coordinator will mark that worker unavailable. Check logs:

```bash
docker logs control
```

Look for lines like `[registry] worker-1 marked unavailable (last heartbeat Xs ago)`.

Any requests that were in-flight on the stopped worker will be retried automatically on the remaining workers.

---

## Local Development (docker-compose)

For local development without real GPU nodes:

```bash
cp .env.example .env   # edit as needed
docker compose up
```

`docker-compose.yml` starts Chroma, one control container, and one worker container on the same machine. Ollama must be running locally (`ollama serve`) before starting the worker.
