#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/eval_common.sh"

LEVELS="${LEVELS:-100 250 500 1000}"
STRATEGIES="${STRATEGIES:-least_tasks lowest_resource fastest_response gpu_aware}"
PDF_STRATEGY="${PDF_STRATEGY:-gpu_aware}"
STRATEGY_USERS="${STRATEGY_USERS:-500}"
REQUESTS_MULTIPLIER="${REQUESTS_MULTIPLIER:-1}"
RUN_FAULT_TEST="${RUN_FAULT_TEST:-false}"

init_results_dir
check_system_ready
snapshot "pdf_before"

log "PDF evaluation: strategy comparison at users=$STRATEGY_USERS"
for strategy in $STRATEGIES; do
  run_load "pdf_strategy_${strategy}_${STRATEGY_USERS}" "$STRATEGY_USERS" "$STRATEGY_USERS" "$strategy"
done

log "PDF evaluation: load scaling for strategy=$PDF_STRATEGY levels=$LEVELS"
for users in $LEVELS; do
  requests=$((users * REQUESTS_MULTIPLIER))
  run_load "pdf_load_${PDF_STRATEGY}_${users}" "$users" "$requests" "$PDF_STRATEGY"
done

if [[ "$RUN_FAULT_TEST" == "true" ]]; then
  log "Fault test requested. Run scripts/run_fault_tolerance_test.sh separately for interactive worker stop capture."
fi

snapshot "pdf_after"
python3 scripts/summarize_results.py "$RESULTS_DIR" | tee "$RESULTS_DIR/summary.md"
log "PDF evaluation complete. Summary: $RESULTS_DIR/summary.md"
