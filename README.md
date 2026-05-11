# Distributed RAG LLM Cluster

Multi-node distributed RAG and LLM inference system for CSE 354.

```
Client → NGINX (Mac) → Coordinator → Chroma RAG → Best GPU Worker → Ollama → Response
```

The Mac runs the master: NGINX gateway, coordinator, Chroma vector database, Prometheus, and Grafana.  
Each Thunder Compute GPU node runs Ollama with the LLM model and a worker container that registers with the master.

---

## Part 1 — Mac (Master)

### Prerequisites

- Docker Desktop installed and running
- ngrok installed: `brew install ngrok/ngrok/ngrok`

### Step 1 — Create your environment file

```bash
cp .env.example .env
```

No changes needed. The defaults work for the master.

### Step 2 — Start ngrok

Open a **new terminal** and run:

```bash
ngrok http 80
```

Leave this terminal open. Copy the HTTPS forwarding URL it shows, for example:

```
https://abc123.ngrok-free.app
```

You will need this URL when setting up each GPU node.

### Step 3 — Start the master

Back in your main terminal, from the project root:

```bash
docker compose --profile master up --build -d
```

The first build takes 10–30 minutes because it downloads and installs `sentence-transformers` and its dependencies. Subsequent builds use the Docker cache and finish in seconds.

### Step 4 — Confirm the master is running

```bash
curl http://localhost/health
```

Expected response:

```json
{"status": "healthy", "healthy_workers": 0}
```

`healthy_workers: 0` is normal. Workers will appear once GPU nodes connect.

---

## Part 2 — Thunder Compute GPU Node (Worker)

Do these steps once per GPU node. SSH into the node first.

### Step 1 — Clone the repo

```bash
gh repo clone YousifHisham/cse354-distributed-rag-llm
cd cse354-distributed-rag-llm
```

### Step 2 — Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Step 3 — Start Ollama in the background

Thunder Compute does not run systemd, so start Ollama manually:

```bash
nohup ollama serve > /tmp/ollama.log 2>&1 &
sleep 3
```

Confirm it is running:

```bash
curl http://localhost:11434/api/tags
```

You should get a JSON response. If you get "connection refused", wait a few more seconds and try again.

### Step 4 — Pull the model

```bash
ollama pull llama3.1:8b
```

This downloads ~4.7 GB. It takes a few minutes depending on the node's network speed.

### Step 5 — Start the worker

```bash
./scripts/start_worker.sh <ngrok HTTPS URL from the Mac> gpu-worker-1
```

For example:

```bash
./scripts/start_worker.sh https://abc123.ngrok-free.app gpu-worker-1
```

The script auto-detects this node's public IP, builds the worker image, and starts the container. No env file needed on the GPU node.

The first build takes a few minutes. Subsequent builds are instant.

### Step 6 — Confirm the worker registered

From the **Mac**, run:

```bash
curl http://localhost/health
curl http://localhost/workers
```

`healthy_workers` should now be `1`. The `/workers` response lists the worker with its name, IP, and GPU stats.

---

## Adding a Second GPU Node

Repeat Part 2 on the second node. In Step 5 use a different worker name:

```bash
./scripts/start_worker.sh https://abc123.ngrok-free.app gpu-worker-2
```

---

## Send a Query

Once at least one worker is registered:

```bash
curl -X POST http://localhost/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What is distributed computing?"}'
```

The response includes the answer, which worker handled it, latency, and retry count.

---

## Dashboards

| Dashboard | URL | Default login |
|---|---|---|
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | — |

---

## Stop Everything

```bash
# Mac
docker compose --profile master down

# GPU node
docker compose --profile worker down
```

---

## Useful Debug Commands

```bash
# Master logs
docker compose logs -f control

# Worker logs (on GPU node)
docker compose --profile worker logs -f worker

# Check what models are loaded on the GPU node
ollama list

# Check Ollama API directly
curl http://localhost:11434/api/tags

# Change load-balancing strategy without restarting
curl -X POST http://localhost/config/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "gpu_aware"}'
```

Valid strategies: `least_tasks` · `lowest_resource` · `fastest_response` · `gpu_aware`

---

## Load Tests

```bash
# Single targeted test
python3 scripts/load_generator.py \
  --url http://localhost \
  --users 100 \
  --requests 100 \
  --queries-file scripts/queries.txt \
  --out-dir results/manual \
  --label gpu_aware_100

# Full strategy comparison
USERS=500 REQUESTS=500 ./scripts/run_strategy_comparison.sh

# PDF evaluation suite
./scripts/run_pdf_evaluation.sh
```

Results are written under `results/` with per-request JSONL files, summaries, and Prometheus snapshots.
