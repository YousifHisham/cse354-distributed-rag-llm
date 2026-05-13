# Distributed RAG LLM Cluster

Multi-node distributed RAG and LLM inference system for CSE 354.

```text
Client scripts -> NGINX/control container -> Coordinator -> worker-1..5 -> Thunder Compute Ollama
                                                   |
                                                   v
                                                Chroma RAG
```

Everything except Ollama runs locally on the Mac in Docker. Each Thunder Compute node runs Ollama and the optional GPU metrics helper.

## What Runs Where

| Component | Runs on | Purpose |
|---|---|---|
| `control` | Mac Docker | NGINX, FastAPI coordinator, scheduler, RAG lookup, Prometheus endpoint |
| `chroma` | Mac Docker | Vector database for RAG knowledge chunks |
| `worker-1..5` | Mac Docker | Worker APIs that call remote Thunder Ollama endpoints |
| `prometheus` | Mac Docker | Metrics collection |
| `grafana` | Mac Docker | Dashboards |
| `ollama` | Thunder Compute | Actual LLM inference |
| `scripts/thunder_metrics.py` | Thunder Compute | Optional real GPU stats via `nvidia-smi` |

## Prerequisites

On the Mac:

- Docker Desktop
- Python 3
- `curl`

On each Thunder Compute node:

- SSH access
- GPU instance with `nvidia-smi`
- Ports `11434` and `8888` exposed/forwarded in Thunder Compute

## 1. Bootstrap Each Thunder Node

Repeat this section for every Thunder Compute instance.

If the repo is already on the Thunder node:

```bash
ssh ubuntu@YOUR_THUNDER_NODE
cd /path/to/project
chmod +x scripts/bootstrap_thunder_node.sh
./scripts/bootstrap_thunder_node.sh llama3.1:8b
```

If the repo is not on the Thunder node, copy only the bootstrap files:

```bash
scp scripts/bootstrap_thunder_node.sh scripts/thunder_metrics.py ubuntu@YOUR_THUNDER_NODE:~/
ssh ubuntu@YOUR_THUNDER_NODE
mkdir -p scripts
mv bootstrap_thunder_node.sh thunder_metrics.py scripts/
chmod +x scripts/bootstrap_thunder_node.sh scripts/thunder_metrics.py
./scripts/bootstrap_thunder_node.sh llama3.1:8b
```

The bootstrap script:

- installs Ollama if it is missing
- installs `nvidia-modprobe` if it is missing
- loads the NVIDIA UVM driver with `sudo nvidia-modprobe -u`
- starts Ollama on `0.0.0.0:11434`
- pulls `llama3.1:8b`
- starts GPU metrics on `0.0.0.0:8888`
- writes logs to `~/thunder-llm-logs/`

In Thunder Compute, expose/forward:

```text
11434 -> Ollama
8888  -> GPU metrics
```

Record each instance's public URLs, for example:

```env
THUNDER_COMPUTE_URL_1=https://abc123-11434.thundercompute.net
THUNDER_METRICS_URL_1=https://abc123-8888.thundercompute.net
```

It is okay if multiple Thunder nodes show the same raw public IP. Use the unique Thunder forwarded URL/subdomain for each node. Do not reuse the exact same `https://...-11434...` URL for multiple workers.

## 2. Configure the Mac Environment

From the project root on the Mac:

```bash
cp .env.example .env
```

Edit `.env` and fill in all Thunder URLs:

```env
LB_STRATEGY=gpu_aware
LLM_MODEL=llama3.1:8b
OLLAMA_MAX_OUTPUT_TOKENS=300
OLLAMA_KEEP_ALIVE=30m
OLLAMA_MAX_CONCURRENT_REQUESTS=4

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

`THUNDER_METRICS_URL_*` is optional for inference, but needed for real GPU temperature/VRAM metrics. Without it, workers still run and use limited Ollama/token-based metrics.

## 3. Start the Local Cluster

Start master services:

```bash
docker compose --profile master up --build -d
```

Start workers:

```bash
docker compose --profile workers up --build -d
```

Check containers:

```bash
docker compose ps
```

Check coordinator health:

```bash
curl http://localhost/health
```

Expected when all five workers are ready:

```json
{"status":"ok","healthy_workers":5,"total_workers":5}
```

`total_workers` is the number of registered local worker containers. `healthy_workers` counts only workers that can reach their configured Thunder Ollama endpoint and find `LLM_MODEL`.

## 4. Run a Single Request Test

```bash
python3 scripts/single_request.py "What is the CAP theorem?"
```

This prints:

- answer
- request id
- worker id
- latency
- retry count
- whether RAG was used
- RAG context size
- RAG sources

You can also run the single-request client through Docker:

```bash
docker compose --profile client run --rm client
```

## 5. Run a Generic Load Test

Default load test:

```bash
python3 scripts/load_test.py
```

Custom request count:

```bash
python3 scripts/load_test.py --requests 100
```

Pick a load-balancing strategy:

```bash
python3 scripts/load_test.py --requests 100 --strategy gpu_aware
```

Valid strategies:

```text
least_tasks
lowest_resource
fastest_response
gpu_aware
```

The load test prints every request with:

- request id
- worker
- latency
- retries
- question
- answer preview
- RAG used/not used
- RAG context size
- RAG sources
- timing breakdown for RAG, queueing, worker roundtrip, worker wait, and Ollama generation
- token speed, with `CPU-LIKE?` warning when generation is below the configured threshold

It also prints a final summary with success rate, throughput, latency percentiles, retries, worker distribution, RAG usage, source counts, slowest requests, and failures.

To print full answers instead of 300-character previews:

```bash
ANSWER_CHARS=0 python3 scripts/load_test.py --requests 100
```

To save summary JSON:

```bash
python3 scripts/load_test.py \
  --requests 100 \
  --strategy gpu_aware \
  --out-dir results/manual_100 \
  --label gpu_aware_100
```

If token speed is around `2-5 tok/s`, that Thunder node is probably running on CPU or not using the GPU correctly. A healthy RTX A6000 node should be much faster for this model.

## 5.1. Verify Thunder GPU Usage

On each Thunder node, run this in one SSH terminal:

```bash
watch -n 0.5 nvidia-smi
```

In a second SSH terminal on the same node, force a local generation:

```bash
curl http://127.0.0.1:11434/api/generate \
  -d '{"model":"llama3.1:8b","prompt":"Explain distributed computing in one paragraph.","stream":false,"keep_alive":"30m"}'
```

Then check:

```bash
ollama ps
```

Good signs:

- `nvidia-smi` shows higher GPU utilization during generation
- Ollama GPU memory rises above idle/runtime memory
- `ollama ps` shows the model loaded and using GPU

Bad signs:

- GPU utilization stays at `0%`
- Ollama only uses around `1GB` VRAM while generating
- `ollama ps` is empty after generation
- load-test output shows `CPU-LIKE?` or token speed around `2-5 tok/s`

If a node looks bad, rerun:

```bash
./scripts/bootstrap_thunder_node.sh llama3.1:8b
```

## 6. Run 1000 Requests for Every Strategy

```bash
python3 scripts/strategy_1000_test.py
```

This runs 1000 simultaneous requests for each strategy:

```text
least_tasks
lowest_resource
fastest_response
gpu_aware
```

It captures before/after evidence from:

- `/health`
- `/workers`
- `/debug/state`
- `/config/strategy`
- `/prometheus`

Outputs are written under:

```text
results/strategy_1000_<timestamp>/
```

Useful options:

```bash
python3 scripts/strategy_1000_test.py --requests 1000
python3 scripts/strategy_1000_test.py --url http://localhost
python3 scripts/strategy_1000_test.py --results-dir results/my_strategy_run
```

## 7. Run the Fault Recovery Test

```bash
python3 scripts/fault_recovery_test.py
```

The script will:

1. Capture cluster state before failure.
2. Wait for you to press Enter.
3. Start a 500-request `gpu_aware` load test.
4. Tell you to stop one worker in another terminal:

```bash
docker compose stop worker-1
```

5. Capture state after the run.
6. Try to restart the worker automatically:

```bash
docker compose start worker-1
```

7. Capture recovery state.

Outputs are written under:

```text
results/fault_tolerance_<timestamp>/
```

Useful options:

```bash
python3 scripts/fault_recovery_test.py --requests 500
python3 scripts/fault_recovery_test.py --worker worker-2
python3 scripts/fault_recovery_test.py --strategy gpu_aware
python3 scripts/fault_recovery_test.py --results-dir results/my_fault_run
```

## 8. Dashboards

| Dashboard | URL | Login |
|---|---|---|
| Grafana | http://localhost:3000 | `admin` / `admin` by default |
| Prometheus | http://localhost:9090 | none |
| Coordinator Prometheus endpoint | http://localhost/prometheus | none |

## 9. Useful Debug Commands

Container status:

```bash
docker compose ps
```

Coordinator logs:

```bash
docker compose logs -f control
```

Worker logs:

```bash
docker compose logs -f worker-1
```

Registered workers:

```bash
curl http://localhost/workers
```

Queue, strategy, active requests, worker state:

```bash
curl http://localhost/debug/state
```

Current strategy:

```bash
curl http://localhost/config/strategy
```

Change strategy live:

```bash
curl -X POST http://localhost/config/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "gpu_aware"}'
```

Check a Thunder Ollama endpoint:

```bash
curl https://YOUR_NODE-11434.thundercompute.net/api/tags
```

Check a Thunder metrics endpoint:

```bash
curl https://YOUR_NODE-8888.thundercompute.net/
```

## 10. Stop Everything

Stop local Docker services:

```bash
docker compose --profile master --profile workers down
```

Stop one worker only:

```bash
docker compose stop worker-1
```

Start one worker again:

```bash
docker compose start worker-1
```

## Script Inventory

| Script | Purpose |
|---|---|
| `scripts/single_request.py` | Sends one query and prints response, worker, latency, retries, and RAG details |
| `scripts/load_test.py` | Sends N simultaneous requests, prints every response, and prints a detailed summary |
| `scripts/strategy_1000_test.py` | Runs the load test across all strategies, defaulting to 1000 requests per strategy |
| `scripts/fault_recovery_test.py` | Runs a load test while one worker is stopped and captures recovery evidence |
| `scripts/bootstrap_thunder_node.sh` | Runs on Thunder to install/start Ollama and metrics |
| `scripts/thunder_metrics.py` | Runs on Thunder to expose `nvidia-smi` GPU stats as JSON |
