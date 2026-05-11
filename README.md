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
curl -fsSL https://ollama.com/install.sh | sh
nohup ollama serve > /tmp/ollama.log 2>&1 &
sleep 3
ollama pull llama3.1:8b
```

Note the public Ollama endpoint URL for each instance from your Thunder Compute dashboard (e.g. `https://abc123-11434.thundercompute.net`).

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

---

## Run the Client

Fires 20 requests simultaneously, prints each answer and a summary:

```bash
docker compose --profile client run --rm client
```

Change the number of requests:

```bash
NUM_REQUESTS=50 docker compose --profile client run --rm client
```

---

## Testing

```bash
# Single query from terminal
python3 scripts/query.py

# Load test
python3 scripts/load_test.py
python3 scripts/load_test.py --requests 100 --strategy gpu_aware

# Live cluster dashboard
python3 scripts/watch.py

# Full strategy comparison
./scripts/run_strategy_comparison.sh

# PDF evaluation suite
./scripts/run_pdf_evaluation.sh
```

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
