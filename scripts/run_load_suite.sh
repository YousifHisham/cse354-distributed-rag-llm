#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/eval_common.sh"

LEVELS="${LEVELS:-100 250 500 1000}"
STRATEGY="${STRATEGY:-}"
REQUESTS_MULTIPLIER="${REQUESTS_MULTIPLIER:-1}"

init_results_dir
check_system_ready
set_strategy_if_needed "$STRATEGY"
snapshot "suite_before"

for users in $LEVELS; do
  requests=$((users * REQUESTS_MULTIPLIER))
  active="${STRATEGY:-current}"
  label="load_${active}_${users}"
  run_load "$label" "$users" "$requests" "$STRATEGY"
done

python3 scripts/summarize_results.py "$RESULTS_DIR" | tee "$RESULTS_DIR/summary.md"
log "Load suite complete. Summary: $RESULTS_DIR/summary.md"
