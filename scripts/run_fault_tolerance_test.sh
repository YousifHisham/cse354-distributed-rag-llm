#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/eval_common.sh"

STRATEGY="${STRATEGY:-gpu_aware}"
USERS="${USERS:-100}"
REQUESTS="${REQUESTS:-200}"
FAILURE_DELAY="${FAILURE_DELAY:-10}"

init_results_dir
check_system_ready
set_strategy_if_needed "$STRATEGY"
snapshot "fault_before"

log "Starting fault-tolerance load in background: users=$USERS requests=$REQUESTS strategy=$STRATEGY"
verbose_args=()
if [[ "$VERBOSE_REQUESTS" == "true" ]]; then
  verbose_args+=(--verbose)
fi
query_file_args=()
if [[ -n "${QUERIES_FILE:-}" ]]; then
  query_file_args+=(--queries-file "$QUERIES_FILE")
fi
python3 scripts/load_generator.py \
  --url "$BASE_URL" \
  --strategy "$STRATEGY" \
  --users "$USERS" \
  --requests "$REQUESTS" \
  --timeout "$REQUEST_TIMEOUT" \
  "${query_file_args[@]}" \
  --out-dir "$RESULTS_DIR" \
  --label "fault_${STRATEGY}_${USERS}" \
  "${verbose_args[@]}" \
  > >(tee -a "$RESULTS_DIR/run.log") \
  2> >(tee -a "$RESULTS_DIR/run.log" >&2) &
load_pid=$!

log "Waiting ${FAILURE_DELAY}s before failure action..."
sleep "$FAILURE_DELAY"
snapshot "fault_during_before_manual_stop"

cat <<EOF | tee -a "$RESULTS_DIR/run.log"

Stop one GPU worker now, for example on the GPU node:
  docker stop worker-1

Then press Enter here to continue capturing recovery data.
EOF
read -r _

snapshot "fault_after_manual_stop"
log "Waiting for load generator pid=$load_pid to finish..."
set +e
wait "$load_pid"
status=$?
set -e
snapshot "fault_after_load"

python3 scripts/summarize_results.py "$RESULTS_DIR" | tee "$RESULTS_DIR/summary.md"
log "Fault tolerance test complete with load exit=$status. Summary: $RESULTS_DIR/summary.md"
