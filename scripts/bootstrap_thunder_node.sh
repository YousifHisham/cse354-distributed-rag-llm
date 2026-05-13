#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:-${LLM_MODEL:-llama3.1:8b}}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
METRICS_PORT="${METRICS_PORT:-8888}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
METRICS_SCRIPT="$SCRIPT_DIR/thunder_metrics.py"
LOG_DIR="${LOG_DIR:-$HOME/thunder-llm-logs}"

mkdir -p "$LOG_DIR"

log() {
  printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"
}

if ! command -v ollama >/dev/null 2>&1; then
  log "Installing Ollama"
  curl -fsSL https://ollama.com/install.sh | sh
fi

if ! command -v python3 >/dev/null 2>&1; then
  log "ERROR: python3 is required for GPU metrics"
  exit 1
fi

if [[ ! -f "$METRICS_SCRIPT" ]]; then
  log "ERROR: missing $METRICS_SCRIPT"
  log "Run this script from the repo copy on the Thunder node."
  exit 1
fi

log "Stopping existing Ollama/metrics processes if present"
pkill -f "ollama serve" >/dev/null 2>&1 || true
pkill -f "thunder_metrics.py" >/dev/null 2>&1 || true

log "Starting Ollama on 0.0.0.0:${OLLAMA_PORT}"
OLLAMA_HOST="0.0.0.0:${OLLAMA_PORT}" \
  nohup ollama serve > "$LOG_DIR/ollama.log" 2>&1 &

sleep 3

log "Pulling model: $MODEL"
ollama pull "$MODEL"

log "Starting GPU metrics server on 0.0.0.0:${METRICS_PORT}"
nohup python3 "$METRICS_SCRIPT" --port "$METRICS_PORT" \
  > "$LOG_DIR/thunder_metrics.log" 2>&1 &

sleep 1

log "Bootstrap complete"
log "Ollama local URL:  http://127.0.0.1:${OLLAMA_PORT}"
log "Metrics local URL: http://127.0.0.1:${METRICS_PORT}"
log "Logs:"
log "  $LOG_DIR/ollama.log"
log "  $LOG_DIR/thunder_metrics.log"
log "Forward/expose ports ${OLLAMA_PORT} and ${METRICS_PORT} in Thunder Compute, then use those public URLs in .env."
