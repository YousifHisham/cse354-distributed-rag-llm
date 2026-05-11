# Contract: Coordinator → Worker

**Direction**: Coordinator dispatches inference tasks to individual workers  
**Transport**: HTTP POST to `{WorkerInfo.address}/infer`

---

## POST /infer

Process an augmented prompt and return an LLM-generated answer.

### Request

```
POST /infer
Content-Type: application/json
```

```json
{
  "request_id": "string — coordinator's request UUID (for log correlation)",
  "prompt":     "string — full augmented prompt (user query + RAG context combined)",
  "model":      "string — Ollama model name, e.g. 'llama3.2:8b'"
}
```

### Response — 200 OK

```json
{
  "request_id": "string — echoed from request",
  "answer":     "string — LLM-generated text",
  "latency_ms": 0.0
}
```

### Response — 500 Internal Server Error

Returned if Ollama is unreachable or returns an error.

```json
{
  "detail": "string — error description"
}
```

**Coordinator behaviour on non-200**: marks the request for retry on a different worker. The failed worker is NOT immediately marked unavailable — only missing metrics pushes trigger unavailability.

---

## POST /register  *(worker → coordinator, documented here for completeness)*

Workers call this on startup to join the coordinator's registry.

```
POST {COORDINATOR_URL}/register
Content-Type: application/json
```

```json
{
  "address": "string — http://WORKER_HOST:WORKER_PORT",
  "name":    "string | null — human-friendly worker name",
  "model":   "string | null — Ollama model configured on the worker"
}
```

### Response — 200 OK

```json
{
  "status": "registered",
  "worker_id": "string — coordinator-assigned worker ID"
}
```
