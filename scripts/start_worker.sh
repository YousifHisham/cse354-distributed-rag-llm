#!/usr/bin/env bash
set -euo pipefail

COORDINATOR_URL="${1:-}"
WORKER_NAME="${2:-gpu-worker-1}"

if [[ -z "$COORDINATOR_URL" ]]; then
  echo "Usage: ./scripts/start_worker.sh <coordinator_url> [worker_name]"
  echo ""
  echo "Example:"
  echo "  ./scripts/start_worker.sh https://abc123.ngrok-free.app"
  echo "  ./scripts/start_worker.sh https://abc123.ngrok-free.app gpu-worker-2"
  exit 1
fi

if [[ ! -f "worker/Dockerfile" ]]; then
  echo "ERROR: run this script from the project root."
  exit 1
fi

echo "[worker] Detecting public IP..."
WORKER_HOST="$(curl -fsSL https://api.ipify.org)"

echo "[worker] Starting worker..."
echo "  Name:        $WORKER_NAME"
echo "  Worker IP:   $WORKER_HOST"
echo "  Coordinator: $COORDINATOR_URL"
echo ""

echo "[worker] Building worker image..."
docker build -t distributed-worker ./worker

if docker ps -a --format '{{.Names}}' | grep -Fxq "$WORKER_NAME"; then
  echo "[worker] Removing existing container: $WORKER_NAME"
  docker rm -f "$WORKER_NAME"
fi

echo "[worker] Starting container..."
GPU_FLAGS=""
if docker run --rm --gpus all hello-world >/dev/null 2>&1; then
  GPU_FLAGS="--gpus all"
fi

docker run -d \
  $GPU_FLAGS \
  --network host \
  --restart unless-stopped \
  --name "$WORKER_NAME" \
  -e WORKER_NAME="$WORKER_NAME" \
  -e WORKER_HOST="$WORKER_HOST" \
  -e COORDINATOR_URL="$COORDINATOR_URL" \
  -e WORKER_PORT=8001 \
  -e OLLAMA_BASE_URL=http://localhost:11434 \
  -e LLM_MODEL=llama3.1:8b \
  -e HEARTBEAT_INTERVAL_SECONDS=0.5 \
  -e METRICS_INTERVAL_SECONDS=1 \
  -e REGISTRATION_RETRY_SECONDS=2 \
  -e LATENCY_WINDOW=20 \
  distributed-worker

echo ""
echo "[worker] Done. Check logs with:"
echo "  docker logs -f $WORKER_NAME"
