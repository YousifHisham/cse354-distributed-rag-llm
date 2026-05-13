# Distributed RAG LLM Cluster

Multi-node distributed RAG and LLM inference system for CSE 354.

```
Client → NGINX (Mac) → Coordinator (Mac) → Worker-1..5 (Mac) → Ollama (Thunder Compute × 5)
```

Everything runs on the Mac in Docker. Thunder Compute nodes run Ollama only.

| Container | Role |
|---|---|
| `client` | One-shot request sender |
| `control` | NGINX + coordinator + RAG + scheduler |
| `chroma` | Vector database for RAG |
| `worker-1..5` | Inference workers — each calls a remote Thunder Compute Ollama |
| `prometheus` | Metrics collection |
| `grafana` | Dashboards |

---

## Setup

### Step 1 — Thunder Compute (one per instance, repeat 5 times)

SSH into each Thunder Compute node and run:

```bash
chmod +x scripts/bootstrap_thunder_node.sh
./scripts/bootstrap_thunder_node.sh llama3.1:8b
```

The bootstrap script:

- installs Ollama if needed
- starts Ollama on `0.0.0.0:11434`
- pulls `llama3.1:8b`
- starts the GPU metrics helper on `0.0.0.0:8888`

Expose/forward ports `11434` and `8888` in Thunder Compute. Note both public URLs for each instance, for example:

```env
THUNDER_COMPUTE_URL_1=https://abc123-11434.thundercompute.net
THUNDER_METRICS_URL_1=https://abc123-8888.thundercompute.net
```

### Step 2 — Mac

**Create your env file:**

```bash
cp .env.example .env
```

Edit `.env` and fill in the 5 Thunder Compute URLs:

```env
THUNDER_COMPUTE_URL_1=https://instance1-11434.thundercompute.net
THUNDER_COMPUTE_URL_2=https://instance2-11434.thundercompute.net
THUNDER_COMPUTE_URL_3=https://instance3-11434.thundercompute.net
THUNDER_COMPUTE_URL_4=https://instance4-11434.thundercompute.net
THUNDER_COMPUTE_URL_5=https://instance5-11434.thundercompute.net

THUNDER_METRICS_URL_1=https://instance1-8888.thundercompute.net
THUNDER_METRICS_URL_2=https://instance2-8888.thundercompute.net
THUNDER_METRICS_URL_3=https://instance3-8888.thundercompute.net
THUNDER_METRICS_URL_4=https://instance4-8888.thundercompute.net
THUNDER_METRICS_URL_5=https://instance5-8888.thundercompute.net
```

**Start the master:**

```bash
docker compose --profile master up --build -d
```

**Start the workers:**

```bash
docker compose --profile workers up --build -d
```

**Confirm everything is up:**

```bash
curl http://localhost/health
```

Expected: `{"status":"ok","healthy_workers":5,"total_workers":5}`

`total_workers` counts registered worker containers. `healthy_workers` counts only
workers that can reach their configured Ollama endpoint and find `LLM_MODEL`.

---

## Run a Single Request

```bash
python3 scripts/single_request.py "What is the CAP theorem?"
```

The Docker client profile runs the same single-request script:

```bash
docker compose --profile client run --rm client
```

---

## Testing

```bash
# Single query from terminal
python3 scripts/single_request.py

# Generic load test
python3 scripts/load_test.py
python3 scripts/load_test.py --requests 100 --strategy gpu_aware

# Print full answers instead of 300-character answer previews
ANSWER_CHARS=0 python3 scripts/load_test.py --requests 100

# 1000 requests against every load-balancing strategy
python3 scripts/strategy_1000_test.py

# Fault tolerance evidence run
python3 scripts/fault_recovery_test.py
```

### Script Inventory

| Script | Purpose | Keep? |
|---|---|---|
| `scripts/single_request.py` | Sends one query and prints answer, worker, latency, retry count, RAG usage, context size, and RAG sources. | Yes - single-request script. |
| `scripts/load_test.py` | Sends any chosen number of simultaneous requests, prints every request response/details/RAG access, then prints a summary. | Yes - generic load-test script. |
| `scripts/bootstrap_thunder_node.sh` | Runs on each Thunder node. Starts Ollama on `0.0.0.0:11434`, pulls the model, and starts GPU metrics on `0.0.0.0:8888`. | Yes - Thunder setup script. |
| `scripts/thunder_metrics.py` | Runs on each Thunder node and exposes `nvidia-smi` GPU stats as JSON. | Yes - used by the bootstrap script. |
| `scripts/strategy_1000_test.py` | Runs 1000 simultaneous requests for each strategy: `least_tasks`, `lowest_resource`, `fastest_response`, and `gpu_aware`. | Yes - strategy comparison script. |
| `scripts/fault_recovery_test.py` | Captures snapshots, runs load while a worker is stopped, then restarts the worker and captures recovery evidence. | Yes - fault recovery script. |

---

## Dashboards

| Dashboard | URL | Login |
|---|---|---|
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | — |

---

## Stop Everything

```bash
docker compose --profile master --profile workers down
```

---

## Useful Debug Commands

```bash
# All container status
docker compose ps

# Coordinator logs
docker compose logs -f control

# Worker logs
docker compose logs -f worker-1

# Check registered workers
curl http://localhost/workers

# Check queue and strategy
curl http://localhost/debug/state

# Change load-balancing strategy live
curl -X POST http://localhost/config/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "gpu_aware"}'
```

Valid strategies: `least_tasks` · `lowest_resource` · `fastest_response` · `gpu_aware`
