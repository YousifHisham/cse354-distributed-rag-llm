# Contract: Gateway → Coordinator

**Direction**: NGINX forwards inbound user requests to the coordinator  
**Transport**: HTTP/1.1 reverse proxy (NGINX upstream to `localhost:$COORDINATOR_PORT`)

---

## POST /query

Accept a user question and return an AI-generated, knowledge-augmented answer.

### Request

```
POST /query
Content-Type: application/json
```

```json
{
  "query": "string — the user's natural-language question"
}
```

### Response — 200 OK

```json
{
  "request_id": "string — UUID assigned by coordinator",
  "answer": "string — LLM-generated answer",
  "latency_ms": 0.0,
  "worker_id": "string — which worker processed it",
  "retry_count": 0
}
```

### Response — 503 Service Unavailable

Returned when no healthy workers are available at time of dispatch.

```json
{
  "detail": "No healthy workers available"
}
```

### Response — 504 Gateway Timeout

Returned when the request exhausted all retries without a successful response.

```json
{
  "detail": "Request failed after {n} retries"
}
```

---

## GET /health

Liveness check — used by NGINX upstream health checking (optional).

### Response — 200 OK

```json
{
  "status": "ok",
  "healthy_workers": 2,
  "total_workers": 3
}
```
