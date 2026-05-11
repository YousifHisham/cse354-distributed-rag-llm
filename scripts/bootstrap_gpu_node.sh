#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/bootstrap_gpu_node.sh <coordinator_url> [worker_name] [model]

Example:
  ./scripts/bootstrap_gpu_node.sh https://abc123.ngrok-free.app gpu-a6000-1 llama3.2:8b

Environment overrides:
  COORDINATOR_URL             Public control-plane URL, usually your ngrok URL
  WORKER_NAME                 Human-friendly worker name, default: gpu-worker
  LLM_MODEL                   Ollama model to pull/use, default: llama3.2:8b
  WORKER_PORT                 Worker API port, default: 8001
  WORKER_HOST                 Public IP/DNS the Mac coordinator can call back
  OLLAMA_BASE_URL             Ollama URL on this GPU node, default: http://localhost:11434
  WORKER_IMAGE                Docker image name, default: distributed-worker
  WORKER_CONTAINER            Docker container name, default: same as WORKER_NAME
  INSTALL_DOCKER              Install Docker if missing, default: true
  ENABLE_DOCKER_GPU           Try Docker --gpus all for worker metrics, default: true
  HEARTBEAT_INTERVAL_SECONDS  Lightweight heartbeat interval, default: 0.5
  METRICS_INTERVAL_SECONDS    Metrics push interval, default: 1
  REGISTRATION_RETRY_SECONDS  Coordinator registration retry interval, default: 2
  LATENCY_WINDOW              Rolling latency window, default: 20

Run this from the project root after copying the repository to the GPU node.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

COORDINATOR_URL="${1:-${COORDINATOR_URL:-}}"
WORKER_NAME="${2:-${WORKER_NAME:-gpu-worker}}"
LLM_MODEL="${3:-${LLM_MODEL:-llama3.2:8b}}"
WORKER_PORT="${WORKER_PORT:-8001}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
WORKER_IMAGE="${WORKER_IMAGE:-distributed-worker}"
WORKER_CONTAINER="${WORKER_CONTAINER:-$WORKER_NAME}"
INSTALL_DOCKER="${INSTALL_DOCKER:-true}"
ENABLE_DOCKER_GPU="${ENABLE_DOCKER_GPU:-true}"
HEARTBEAT_INTERVAL_SECONDS="${HEARTBEAT_INTERVAL_SECONDS:-0.5}"
METRICS_INTERVAL_SECONDS="${METRICS_INTERVAL_SECONDS:-1}"
REGISTRATION_RETRY_SECONDS="${REGISTRATION_RETRY_SECONDS:-2}"
LATENCY_WINDOW="${LATENCY_WINDOW:-20}"

if [[ -z "$COORDINATOR_URL" ]]; then
  echo "ERROR: coordinator URL is required."
  echo
  usage
  exit 1
fi

if [[ ! -d "worker" || ! -f "worker/Dockerfile" ]]; then
  echo "ERROR: run this script from the project root where ./worker/Dockerfile exists."
  exit 1
fi

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: '$cmd' is required but was not found."
    exit 1
  fi
}

require_cmd curl

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1; then
    echo "[gpu-node] Docker already installed: $(docker --version)"
  elif [[ "$INSTALL_DOCKER" == "true" ]]; then
    echo "[gpu-node] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
  else
    echo "ERROR: Docker is required but not installed. Set INSTALL_DOCKER=true or install Docker manually."
    exit 1
  fi

  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl enable docker >/dev/null 2>&1 || true
    sudo systemctl start docker >/dev/null 2>&1 || true
  fi

  if ! docker info >/dev/null 2>&1; then
    echo "[gpu-node] Docker exists but is not usable by this user."
    if sudo docker info >/dev/null 2>&1; then
      echo "[gpu-node] Adding $USER to docker group for future sessions."
      sudo usermod -aG docker "$USER" || true
      echo "ERROR: Docker currently requires sudo. Log out/in, then rerun this script."
      echo "       Or run the script with sudo for this first setup."
      exit 1
    fi
    echo "ERROR: Docker daemon is not running or not reachable."
    exit 1
  fi
}

install_docker_if_needed

if ! command -v ollama >/dev/null 2>&1; then
  echo "[gpu-node] Installing Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "[gpu-node] Ollama already installed: $(ollama --version || true)"
fi

echo "[gpu-node] Starting Ollama service..."
if command -v systemctl >/dev/null 2>&1 && systemctl is-system-running >/dev/null 2>&1; then
  sudo systemctl enable ollama >/dev/null 2>&1 || true
  sudo systemctl restart ollama >/dev/null 2>&1 || true
else
  nohup ollama serve >/tmp/ollama.log 2>&1 &
fi

echo "[gpu-node] Waiting for Ollama at $OLLAMA_BASE_URL..."
for attempt in $(seq 1 30); do
  if curl -fsS "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == "30" ]]; then
    echo "ERROR: Ollama did not become ready at $OLLAMA_BASE_URL."
    exit 1
  fi
  sleep 2
done

echo "[gpu-node] Pulling Ollama model: $LLM_MODEL"
ollama pull "$LLM_MODEL"

if [[ -z "${WORKER_HOST:-}" ]]; then
  echo "[gpu-node] Detecting public worker IP..."
  WORKER_HOST="$(curl -fsS https://api.ipify.org || true)"
fi

if [[ -z "${WORKER_HOST:-}" ]]; then
  echo "ERROR: Could not auto-detect WORKER_HOST."
  echo "Set WORKER_HOST to the IP/DNS your Mac coordinator can reach."
  exit 1
fi

echo "[gpu-node] Building worker image: $WORKER_IMAGE"
docker build -t "$WORKER_IMAGE" ./worker

ENV_FILE=".gpu-worker.env"
cat > "$ENV_FILE" <<EOF
WORKER_NAME=$WORKER_NAME
WORKER_PORT=$WORKER_PORT
WORKER_HOST=$WORKER_HOST
COORDINATOR_URL=$COORDINATOR_URL
OLLAMA_BASE_URL=$OLLAMA_BASE_URL
LLM_MODEL=$LLM_MODEL
HEARTBEAT_INTERVAL_SECONDS=$HEARTBEAT_INTERVAL_SECONDS
METRICS_INTERVAL_SECONDS=$METRICS_INTERVAL_SECONDS
REGISTRATION_RETRY_SECONDS=$REGISTRATION_RETRY_SECONDS
LATENCY_WINDOW=$LATENCY_WINDOW
EOF

echo "[gpu-node] Worker environment:"
sed 's/^/  /' "$ENV_FILE"

if docker ps -a --format '{{.Names}}' | grep -Fxq "$WORKER_CONTAINER"; then
  echo "[gpu-node] Replacing existing worker container: $WORKER_CONTAINER"
  docker rm -f "$WORKER_CONTAINER" >/dev/null
fi

echo "[gpu-node] Starting worker container: $WORKER_CONTAINER"
if [[ "$ENABLE_DOCKER_GPU" == "true" ]]; then
  if docker run -d \
    --gpus all \
    --env-file "$ENV_FILE" \
    --network host \
    --restart unless-stopped \
    --name "$WORKER_CONTAINER" \
    "$WORKER_IMAGE"; then
    echo "[gpu-node] Worker started with Docker GPU access for nvidia-smi metrics."
  else
    echo "[gpu-node] Docker GPU access failed; falling back without --gpus all."
    docker rm -f "$WORKER_CONTAINER" >/dev/null 2>&1 || true
    docker run -d \
      --env-file "$ENV_FILE" \
      --network host \
      --restart unless-stopped \
      --name "$WORKER_CONTAINER" \
      "$WORKER_IMAGE"
  fi
else
  docker run -d \
    --env-file "$ENV_FILE" \
    --network host \
    --restart unless-stopped \
    --name "$WORKER_CONTAINER" \
    "$WORKER_IMAGE"
fi

echo
echo "[gpu-node] Done."
echo "  Worker name:      $WORKER_NAME"
echo "  Worker address:   http://$WORKER_HOST:$WORKER_PORT"
echo "  Coordinator URL:  $COORDINATOR_URL"
echo "  Ollama model:     $LLM_MODEL"
echo
echo "Useful checks:"
echo "  docker logs -f $WORKER_CONTAINER"
echo "  curl http://$WORKER_HOST:$WORKER_PORT/docs"
