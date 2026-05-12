from __future__ import annotations

import asyncio
import collections
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
import psutil
from fastapi import FastAPI, HTTPException

from models import (
    InferRequest,
    InferResponse,
    WorkerHeartbeat,
    WorkerMetrics,
    WorkerRegistration,
    WorkerRegistrationResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
WORKER_NAME               = os.environ.get("WORKER_NAME", "worker-1")
WORKER_PORT               = int(os.environ.get("WORKER_PORT", "8001"))
WORKER_HOST               = os.environ.get("WORKER_HOST", WORKER_NAME)
WORKER_ADDRESS            = os.environ.get("WORKER_ADDRESS", f"http://{os.environ.get('WORKER_HOST', WORKER_NAME)}:{os.environ.get('WORKER_PORT', '8001')}")
COORDINATOR_URL           = os.environ.get("COORDINATOR_URL", "http://control:8000")
OLLAMA_BASE_URL           = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_SSL_VERIFY         = os.environ.get("OLLAMA_SSL_VERIFY", "true").lower() != "false"
LLM_MODEL                 = os.environ.get("LLM_MODEL", "llama3.1:8b")
METRICS_INTERVAL_SECONDS  = float(os.environ.get("METRICS_INTERVAL_SECONDS", "1"))
HEARTBEAT_INTERVAL_SECONDS= float(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "0.5"))
REGISTRATION_RETRY_SECONDS= float(os.environ.get("REGISTRATION_RETRY_SECONDS", "2"))
LATENCY_WINDOW            = int(os.environ.get("LATENCY_WINDOW", "20"))

# Tokens/s above this value = GPU considered fully utilized (normalize to 0-1)
MAX_TOKENS_PER_SECOND = float(os.environ.get("MAX_TOKENS_PER_SECOND", "80.0"))

# ── Metrics state ─────────────────────────────────────────────────────────────
ASSIGNED_WORKER_ID: str | None = None
_active_tasks: int = 0
_completed_tasks: int = 0
_failed_tasks: int = 0
_latency_window: collections.deque[float] = collections.deque(maxlen=LATENCY_WINDOW)
_tokens_per_second: float = 0.0   # derived from Ollama response metadata
_vram_used_gb: float = 0.0        # from Ollama /api/ps
_vram_total_gb: float = 0.0       # from Ollama /api/ps
_shutdown: bool = False
_http_client: httpx.AsyncClient | None = None


# ── Ollama GPU metrics ────────────────────────────────────────────────────────
async def _poll_ollama_gpu() -> None:
    """Poll Ollama /api/ps for VRAM usage of the loaded model."""
    global _vram_used_gb, _vram_total_gb
    try:
        r = await _http_client.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=5)
        if r.status_code == 200:
            models = r.json().get("models", [])
            if models:
                vram_bytes = sum(m.get("size_vram", 0) for m in models)
                size_bytes = sum(m.get("size", 0) for m in models)
                _vram_used_gb = vram_bytes / 1024**3
                _vram_total_gb = size_bytes / 1024**3
    except Exception:
        pass


def _gpu_utilization() -> float:
    """Normalize tokens/s to a 0.0-1.0 utilization score."""
    return min(_tokens_per_second / MAX_TOKENS_PER_SECOND, 1.0)


def _parse_ollama_metadata(data: dict) -> None:
    """Extract tokens/second from Ollama generate response."""
    global _tokens_per_second
    eval_count    = data.get("eval_count", 0)
    eval_duration = data.get("eval_duration", 0)  # nanoseconds
    if eval_count and eval_duration:
        _tokens_per_second = eval_count / (eval_duration / 1e9)


# ── Registration, heartbeat, metrics ─────────────────────────────────────────
async def _register() -> None:
    global ASSIGNED_WORKER_ID
    body = WorkerRegistration(
        address=WORKER_ADDRESS,
        name=WORKER_NAME,
        model=LLM_MODEL,
    ).model_dump()
    while True:
        try:
            r = await _http_client.post(f"{COORDINATOR_URL}/register", json=body)
            if r.status_code == 200:
                ASSIGNED_WORKER_ID = WorkerRegistrationResponse(**r.json()).worker_id
                logger.info("[worker] registered as %s", ASSIGNED_WORKER_ID)
                return
            logger.warning("[worker] unexpected register response: %d", r.status_code)
        except Exception as exc:
            logger.warning("[worker] registration failed (%s), retrying in %.0fs", exc, REGISTRATION_RETRY_SECONDS)
        await asyncio.sleep(REGISTRATION_RETRY_SECONDS)


async def _push_heartbeat() -> None:
    while not _shutdown:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        if ASSIGNED_WORKER_ID is None:
            await _register()
            continue
        try:
            r = await _http_client.post(
                f"{COORDINATOR_URL}/heartbeat",
                json=WorkerHeartbeat(
                    worker_id=ASSIGNED_WORKER_ID,
                    active_tasks=_active_tasks,
                    gpu_utilization=_gpu_utilization(),
                    vram_used_gb=_vram_used_gb,
                    vram_total_gb=_vram_total_gb,
                    gpu_temperature_c=0.0,
                    timestamp=time.time(),
                ).model_dump(),
            )
            if r.status_code == 404:
                logger.warning("[worker] coordinator 404 on /heartbeat — re-registering")
                await _register()
        except Exception as exc:
            logger.warning("[worker] heartbeat failed: %s", exc)


async def _push_metrics() -> None:
    while not _shutdown:
        await asyncio.sleep(METRICS_INTERVAL_SECONDS)
        if ASSIGNED_WORKER_ID is None:
            await _register()
            continue
        await _poll_ollama_gpu()
        try:
            avg_lat = sum(_latency_window) / len(_latency_window) if _latency_window else 0.0
            r = await _http_client.post(
                f"{COORDINATOR_URL}/metrics",
                json=WorkerMetrics(
                    worker_id=ASSIGNED_WORKER_ID,
                    active_tasks=_active_tasks,
                    resource_utilization=psutil.cpu_percent(interval=None) / 100.0,
                    avg_latency_ms=avg_lat,
                    completed_tasks=_completed_tasks,
                    failed_tasks=_failed_tasks,
                    gpu_utilization=_gpu_utilization(),
                    vram_used_gb=_vram_used_gb,
                    vram_total_gb=_vram_total_gb,
                    gpu_temperature_c=0.0,
                    timestamp=time.time(),
                ).model_dump(),
            )
            if r.status_code == 404:
                logger.warning("[worker] coordinator 404 on /metrics — re-registering")
                await _register()
            else:
                logger.debug(
                    "[worker] metrics pushed active=%d gpu=%.2f tok/s=%.1f vram=%.1fGB",
                    _active_tasks, _gpu_utilization(), _tokens_per_second, _vram_used_gb,
                )
        except Exception as exc:
            logger.warning("[worker] metrics push failed: %s", exc)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client, _shutdown
    _shutdown = False
    _http_client = httpx.AsyncClient(timeout=120.0, verify=OLLAMA_SSL_VERIFY)

    await _register()
    heartbeat_task = asyncio.create_task(_push_heartbeat())
    metrics_task   = asyncio.create_task(_push_metrics())

    logger.info("[worker] %s ready on port %d → ollama=%s", ASSIGNED_WORKER_ID, WORKER_PORT, OLLAMA_BASE_URL)
    yield

    _shutdown = True
    heartbeat_task.cancel()
    metrics_task.cancel()
    for t in (heartbeat_task, metrics_task):
        try:
            await t
        except asyncio.CancelledError:
            pass
    await _http_client.aclose()
    logger.info("[worker] %s shutdown complete", ASSIGNED_WORKER_ID)


app = FastAPI(title=f"Worker {WORKER_NAME}", lifespan=lifespan)


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.post("/infer", response_model=InferResponse)
async def infer(body: InferRequest) -> InferResponse:
    global _active_tasks, _completed_tasks, _failed_tasks

    _active_tasks += 1
    start = time.time()
    try:
        r = await _http_client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": body.model, "prompt": body.prompt, "stream": False},
            timeout=120.0,
        )
        r.raise_for_status()
        data = r.json()
        answer: str = data.get("response", "")

        # Extract GPU performance metrics from Ollama response
        _parse_ollama_metadata(data)

        latency_ms = (time.time() - start) * 1000
        _latency_window.append(latency_ms)
        _completed_tasks += 1
        return InferResponse(request_id=body.request_id, answer=answer, latency_ms=latency_ms)
    except Exception as exc:
        _failed_tasks += 1
        logger.error("[worker] infer failed for %s: %s", body.request_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        _active_tasks -= 1


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "worker_id": ASSIGNED_WORKER_ID,
        "worker_name": WORKER_NAME,
        "ollama": OLLAMA_BASE_URL,
        "active_tasks": _active_tasks,
        "completed_tasks": _completed_tasks,
        "tokens_per_second": round(_tokens_per_second, 1),
        "vram_used_gb": round(_vram_used_gb, 2),
    }
