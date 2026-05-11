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

echo "[worker] Installing Python dependencies..."
pip install -q -r worker/requirements.txt

export WORKER_NAME="$WORKER_NAME"
export WORKER_HOST="$WORKER_HOST"
export COORDINATOR_URL="$COORDINATOR_URL"
export WORKER_PORT=8001
export OLLAMA_BASE_URL=http://localhost:11434
export LLM_MODEL=llama3.1:8b
export HEARTBEAT_INTERVAL_SECONDS=0.5
export METRICS_INTERVAL_SECONDS=1
export REGISTRATION_RETRY_SECONDS=2
export LATENCY_WINDOW=20

LOG_FILE="/tmp/${WORKER_NAME}.log"
echo "[worker] Starting worker (logs: $LOG_FILE)..."

PYTHONPATH="$(pwd)" nohup python3 -m uvicorn worker.main:app \
  --host 0.0.0.0 \
  --port "$WORKER_PORT" \
  --log-level info \
  > "$LOG_FILE" 2>&1 &

echo $! > "/tmp/${WORKER_NAME}.pid"
sleep 2

if kill -0 "$(cat /tmp/${WORKER_NAME}.pid)" 2>/dev/null; then
  echo ""
  echo "[worker] Done. Worker is running."
  echo "  Logs : tail -f $LOG_FILE"
  echo "  Stop : kill \$(cat /tmp/${WORKER_NAME}.pid)"
else
  echo "ERROR: Worker failed to start. Check logs: $LOG_FILE"
  exit 1
fi
