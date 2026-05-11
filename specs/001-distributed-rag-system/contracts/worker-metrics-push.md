# Contract: Worker Metrics Push

**Direction**: Worker → Coordinator (periodic push)  
**Transport**: HTTP POST to `{COORDINATOR_URL}/metrics`  
**Cadence**: Every `METRICS_INTERVAL_SECONDS` seconds (worker-side timer)

---

## POST /metrics

Worker reports its current live state to the coordinator. The coordinator uses this snapshot — and only this snapshot — for all routing decisions.

### Request

```
POST /metrics
Content-Type: application/json
```

```json
{
  "worker_id":            "string — coordinator-assigned worker ID",
  "active_tasks":         0,
  "resource_utilization": 0.0,
  "avg_latency_ms":       0.0,
  "completed_tasks":      0,
  "failed_tasks":         0,
  "timestamp":            0.0
}
```

| Field | Range | Notes |
|-------|-------|-------|
| `active_tasks` | ≥ 0 | Requests currently being processed by this worker |
| `resource_utilization` | 0.0 – 1.0 | Fraction of capacity in use; worker reports CPU or GPU utilization, whichever is the bottleneck |
| `avg_latency_ms` | ≥ 0.0 | Rolling average over last `LATENCY_WINDOW` completed requests; 0.0 if no completions yet |
| `completed_tasks` | ≥ 0 | Cumulative since startup |
| `failed_tasks` | ≥ 0 | Cumulative since startup |
| `timestamp` | Unix epoch float | Time the snapshot was collected on the worker |

### Response — 200 OK

```json
{
  "status": "received"
}
```

### Response — 404 Not Found

Returned if `worker_id` is not in the coordinator's registry (worker was not registered first, or coordinator restarted).

```json
{
  "detail": "Unknown worker: {worker_id}"
}
```

**Worker behaviour on 404**: re-send a `POST /register` before resuming metrics push.

---

## Staleness Detection (Coordinator-side)

The worker also sends lightweight heartbeats to `/heartbeat`. The coordinator records `last_heartbeat = time.time()` on every successful heartbeat receipt. A background task runs every `WORKER_STALENESS_SECONDS / 2` seconds and marks any worker whose `last_heartbeat` age exceeds `WORKER_STALENESS_SECONDS` as `unavailable`. When a fresh heartbeat arrives for a previously unavailable worker, status is restored to `healthy`.

Metrics are used for scheduling decisions. Heartbeats are used for liveness.
