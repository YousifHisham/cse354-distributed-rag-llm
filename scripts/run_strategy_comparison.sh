#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/eval_common.sh"

STRATEGIES="${STRATEGIES:-least_tasks lowest_resource fastest_response gpu_aware}"
USERS="${USERS:-500}"
REQUESTS="${REQUESTS:-$USERS}"

init_results_dir
check_system_ready
snapshot "comparison_before"

for strategy in $STRATEGIES; do
  label="strategy_${strategy}_${USERS}"
  run_load "$label" "$USERS" "$REQUESTS" "$strategy"
done

python3 scripts/summarize_results.py "$RESULTS_DIR" | tee "$RESULTS_DIR/summary.md"
log "Strategy comparison complete. Summary: $RESULTS_DIR/summary.md"
