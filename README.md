# Distributed RAG LLM Cluster

Real multi-container distributed RAG and LLM inference system for the CSE 354 project.

The MacBook runs the master side: NGINX gateway, coordinator, Chroma vector database, Prometheus, and Grafana.
Each Thunder Compute GPU node runs the worker side: Ollama on the GPU host plus one worker container that registers with the master and serves real model requests.

## What Runs Where

| Machine | Runs |
| --- | --- |
| MacBook master | NGINX, coordinator API, Chroma DB, Prometheus, Grafana |
| GPU worker node | Docker, Ollama, pulled LLM model, worker container |

The flow is:

```text
Client -> NGINX on MacBook -> Coordinator -> RAG with Chroma -> Best GPU worker -> Ollama -> Response
```

## Master Setup: MacBook

### 1. Install Requirements

Install Docker Desktop on the MacBook.

You also need ngrok so the cloud GPU workers can reach the master:

```bash
brew install ngrok/ngrok/ngrok
```

### 2. Create Environment File

From the project root:

```bash
cp .env.example .env
```

Open `.env` and check these master values:

```env
LB_STRATEGY=gpu_aware
LLM_MODEL=llama3.2:8b
WORKER_STALENESS_SECONDS=3
HEARTBEAT_INTERVAL_SECONDS=0.5
METRICS_INTERVAL_SECONDS=1
```

The model name must match the model pulled on every GPU node.

### 3. Start the Master Containers

```bash
docker compose up -d --build chroma control prometheus grafana
```

Check that the master is alive:

```bash
curl http://localhost/health
```

At this point `healthy_workers` can be `0`. That is normal until GPU workers connect.

### 4. Start ngrok

In another terminal:

```bash
ngrok http 80
```

Copy the HTTPS forwarding URL. It will look like:

```text
https://abc123.ngrok-free.app
```

This URL is the `COORDINATOR_URL` that the GPU workers use.

## Worker Setup: Thunder Compute GPU Node

### 1. Get the Project Onto the GPU Node

SSH into the Thunder Compute node, then clone this repo after it is pushed:

```bash
git clone <your-repo-url>
cd <repo-folder>
```

If you are testing before the repo is pushed, copy the project folder to the GPU node instead.

### 2. Run the One-Command Worker Bootstrap

Use the ngrok URL from the MacBook:

```bash
./scripts/bootstrap_gpu_node.sh https://abc123.ngrok-free.app gpu-worker-1 llama3.2:8b
```

The script will:

- install Docker if it is missing
- install Ollama if it is missing
- start the Ollama service
- pull the selected model
- build the worker Docker image
- start the worker container
- register the worker with the MacBook master

For a second GPU node, use a different name:

```bash
./scripts/bootstrap_gpu_node.sh https://abc123.ngrok-free.app gpu-worker-2 llama3.2:8b
```

If the worker cannot detect its reachable IP, pass it manually:

```bash
WORKER_HOST=<gpu-node-public-ip> ./scripts/bootstrap_gpu_node.sh https://abc123.ngrok-free.app gpu-worker-1 llama3.2:8b
```

## Verify Everything Works

On the MacBook:

```bash
curl http://localhost/health
curl http://localhost/workers
```

You should see the GPU worker registered and receiving heartbeats.

Send a real query:

```bash
curl -X POST http://localhost/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What is distributed computing?"}'
```

The response should include a `request_id`, selected `worker_id`, answer text, latency, and retry count.

## Useful Debug Commands

Master logs:

```bash
docker compose logs -f control
```

Worker logs on the GPU node:

```bash
docker logs -f gpu-worker-1
```

Check Ollama on the GPU node:

```bash
ollama list
curl http://localhost:11434/api/tags
```

Check registered workers from the MacBook:

```bash
curl http://localhost/workers
```

Change load-balancing strategy without restarting:

```bash
curl -X POST http://localhost/config/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "gpu_aware"}'
```

Valid strategies:

- `least_tasks`
- `lowest_resource`
- `fastest_response`
- `gpu_aware`

## Load Tests

Run one debuggable load test:

```bash
python3 scripts/load_generator.py \
  --url http://localhost \
  --users 100 \
  --requests 100 \
  --queries-file scripts/queries.txt \
  --out-dir results/manual \
  --label gpu_aware_100 \
  --verbose
```

Run the full strategy comparison:

```bash
USERS=500 REQUESTS=500 ./scripts/run_strategy_comparison.sh
```

Run the project evaluation suite:

```bash
./scripts/run_pdf_evaluation.sh
```

Results are written under `results/` with summaries, per-request JSONL files, errors, logs, health snapshots, worker snapshots, and Prometheus snapshots.

## Dashboards

Prometheus:

```text
http://localhost:9090
```

Grafana:

```text
http://localhost:3000
```

Default Grafana login:

```text
admin / admin
```

## Stop Everything

Stop the master on the MacBook:

```bash
docker compose down
```

Stop a worker on a GPU node:

```bash
docker stop gpu-worker-1
```

Restart a worker:

```bash
docker start gpu-worker-1
```
