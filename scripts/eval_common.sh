#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost}"
RESULTS_ROOT="${RESULTS_ROOT:-results}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)}"
RESULTS_DIR="${RESULTS_DIR:-$RESULTS_ROOT/$RUN_ID}"
REQUEST_TIMEOUT="${REQUEST_TIMEOUT:-180}"
VERBOSE_REQUESTS="${VERBOSE_REQUESTS:-false}"

log() {
  printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$RESULTS_DIR/run.log"
}

fail() {
  log "ERROR: $*"
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command '$1' not found"
}

init_results_dir() {
  mkdir -p "$RESULTS_DIR"
  : > "$RESULTS_DIR/run.log"
  cat > "$RESULTS_DIR/metadata.json" <<EOF
{
  "run_id": "$RUN_ID",
  "base_url": "$BASE_URL",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  log "Results directory: $RESULTS_DIR"
  log "Base URL: $BASE_URL"
}

fetch() {
  local path="$1"
  local out="$2"
  if curl -fsS "$BASE_URL$path" -o "$out"; then
    log "Saved $path -> $out"
  else
    log "WARN: failed to fetch $path"
    return 1
  fi
}

post_json() {
  local path="$1"
  local body="$2"
  local out="$3"
  if curl -fsS -X POST "$BASE_URL$path" \
    -H "Content-Type: application/json" \
    -d "$body" \
    -o "$out"; then
    log "POST $path -> $out"
  else
    log "WARN: POST $path failed"
    return 1
  fi
}

snapshot() {
  local label="$1"
  fetch "/health" "$RESULTS_DIR/${label}_health.json" || true
  fetch "/workers" "$RESULTS_DIR/${label}_workers.json" || true
  fetch "/debug/state" "$RESULTS_DIR/${label}_debug_state.json" || true
  fetch "/config/strategy" "$RESULTS_DIR/${label}_strategy.json" || true
  fetch "/prometheus" "$RESULTS_DIR/${label}_prometheus.txt" || true
}

healthy_workers() {
  python3 - "$RESULTS_DIR/tmp_health.json" <<'PY'
import json
import sys
try:
    data = json.load(open(sys.argv[1]))
    print(data.get("healthy_workers", 0))
except Exception:
    print(0)
PY
}

check_system_ready() {
  require_cmd curl
  require_cmd python3
  mkdir -p "$RESULTS_DIR"
  if ! fetch "/health" "$RESULTS_DIR/tmp_health.json"; then
    fail "Coordinator is not reachable at $BASE_URL"
  fi
  local healthy
  healthy="$(healthy_workers)"
  rm -f "$RESULTS_DIR/tmp_health.json"
  if [[ "$healthy" == "0" ]]; then
    snapshot "not_ready" || true
    fail "No healthy workers are registered"
  fi
  log "Healthy workers: $healthy"
}

set_strategy_if_needed() {
  local strategy="${1:-}"
  if [[ -n "$strategy" ]]; then
    post_json "/config/strategy" "{\"strategy\":\"$strategy\"}" "$RESULTS_DIR/set_strategy_${strategy}.json"
  else
    fetch "/config/strategy" "$RESULTS_DIR/current_strategy.json" || true
  fi
}

run_load() {
  local label="$1"
  local users="$2"
  local requests="$3"
  local strategy="${4:-}"
  local query="${QUERY:-What is distributed computing?}"

  snapshot "before_${label}"
  log "Running load label=$label users=$users requests=$requests strategy=${strategy:-current}"

  local strategy_args=()
  if [[ -n "$strategy" ]]; then
    strategy_args+=(--strategy "$strategy")
  fi

  set +e
  python3 scripts/load_test.py \
    --requests "$requests" \
    --out-dir "$RESULTS_DIR" \
    --label "$label" \
    "${strategy_args[@]}" \
    2>&1 | tee -a "$RESULTS_DIR/run.log"
  local status=${PIPESTATUS[0]}
  set -e

  snapshot "after_${label}"
  if [[ "$status" != "0" ]]; then
    log "WARN: load run $label completed with failures (exit=$status)"
  else
    log "Load run $label completed successfully"
  fi
}
