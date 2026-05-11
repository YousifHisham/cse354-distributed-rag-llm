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

export COORDINATOR_URL
export WORKER_HOST
export WORKER_NAME

docker compose --profile worker up --build -d

echo ""
echo "[worker] Done. Check logs with:"
echo "  docker compose --profile worker logs -f worker"
