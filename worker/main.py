from __future__ import annotations

import asyncio
import collections
import logging
import os
import subprocess
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
WORKER_NAME: str = os.environ.get("WORKER_NAME", "worker-1")
WORKER_PORT: int = int(os.environ.get("WORKER_PORT", "8001"))
WORKER_HOST: str = os.environ.get("WORKER_HOST", WORKER_NAME)
WORKER_ADDRESS: str = os.environ.get("WORKER_ADDRESS", f"http://{os.environ.get('WORKER_HOST', WORKER_NAME)}:{os.environ.get('WORKER_PORT', '8001')}")
COORDINATOR_URL: str = os.environ.get("COORDINATOR_URL", "http://control:8000")
OLLAMA_SSL_VERIFY: bool = os.environ.get("OLLAMA_SSL_VERIFY", "true").lower() != "false"
OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "llama3.1:8b")
METRICS_INTERVAL_SECONDS: float = float(os.environ.get("METRICS_INTERVAL_SECONDS", "1"))
HEARTBEAT_INTERVAL_SECONDS: float = float(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "0.5"))
REGISTRATION_RETRY_SECONDS: float = float(os.environ.get("REGISTRATION_RETRY_SECONDS", "2"))
LATENCY_WINDOW: int = int(os.environ.get("LATENCY_WINDOW", "20"))
ASSIGNED_WORKER_ID: str | None = None

# ── Metrics state (module-level, updated by endpoint handlers) ────────────────
_active_tasks: int = 0
_completed_tasks: int = 0
_failed_tasks: int = 0
_latency_window: collections.deque[float] = collections.deque(maxlen=LATENCY_WINDOW)
_shutdown: bool = False
_http_client: httpx.AsyncClient | None = None


def _collect_gpu_snapshot() -> dict[str, float]:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=2,
        )
        first_gpu = output.strip().splitlines()[0]
        util_pct, mem_used_mb, mem_total_mb, temp_c = [
            float(part.strip()) for part in first_gpu.split(",")
        ]
        return {
            "gpu_utilization": max(0.0, min(util_pct / 100.0, 1.0)),
            "vram_used_gb": mem_used_mb / 1024.0,
            "vram_total_gb": mem_total_mb / 1024.0,
            "gpu_temperature_c": temp_c,
        }
    except Exception:
        return {
            "gpu_utilization": 0.0,
            "vram_used_gb": 0.0,
            "vram_total_gb": 0.0,
            "gpu_temperature_c": 0.0,
        }


# ── Registration, heartbeat, and metrics push ─────────────────────────────────
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
                registered = WorkerRegistrationResponse(**r.json())
                ASSIGNED_WORKER_ID = registered.worker_id
                logger.info("[worker] registered with coordinator as %s", ASSIGNED_WORKER_ID)
                return
            logger.warning("[worker] unexpected register response: %d", r.status_code)
        except Exception as exc:
            logger.warning(
                "[worker] registration failed (%s), retrying in %.0fs",
                exc, REGISTRATION_RETRY_SECONDS,
            )
        await asyncio.sleep(REGISTRATION_RETRY_SECONDS)


async def _push_heartbeat() -> None:
    global _shutdown
    while not _shutdown:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        if ASSIGNED_WORKER_ID is None:
            await _register()
            continue
        try:
            gpu = _collect_gpu_snapshot()
            heartbeat = WorkerHeartbeat(
                worker_id=ASSIGNED_WORKER_ID,
                active_tasks=_active_tasks,
                **gpu,
                timestamp=time.time(),
            )
            r = await _http_client.post(
                f"{COORDINATOR_URL}/heartbeat",
                json=heartbeat.model_dump(),
            )
            if r.status_code == 404:
                logger.warning("[worker] coordinator returned 404 on /heartbeat — re-registering")
                await _register()
            else:
                logger.debug(
                    "[worker] heartbeat pushed (active=%d)",
                    _active_tasks,
                )
        except Exception as exc:
            logger.warning("[worker] heartbeat push failed: %s", exc)


async def _push_metrics() -> None:
    global _shutdown
    while not _shutdown:
        await asyncio.sleep(METRICS_INTERVAL_SECONDS)
        if ASSIGNED_WORKER_ID is None:
            await _register()
            continue
        try:
            avg_lat = (
                sum(_latency_window) / len(_latency_window)
                if _latency_window
                else 0.0
            )
            metrics = WorkerMetrics(
                worker_id=ASSIGNED_WORKER_ID,
                active_tasks=_active_tasks,
                resource_utilization=psutil.cpu_percent(interval=None) / 100.0,
                avg_latency_ms=avg_lat,
                completed_tasks=_completed_tasks,
                failed_tasks=_failed_tasks,
                **_collect_gpu_snapshot(),
                timestamp=time.time(),
            )
            r = await _http_client.post(
                f"{COORDINATOR_URL}/metrics",
                json=metrics.model_dump(),
            )
            if r.status_code == 404:
                logger.warning("[worker] coordinator returned 404 on /metrics — re-registering")
                await _register()
            else:
                logger.debug(
                    "[worker] metrics pushed (active=%d util=%.2f avg_lat=%.0fms)",
                    _active_tasks,
                    metrics.resource_utilization,
                    avg_lat,
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
    metrics_task = asyncio.create_task(_push_metrics())

    logger.info("[worker] %s ready on port %d", ASSIGNED_WORKER_ID, WORKER_PORT)

    yield

    # Graceful shutdown: stop metrics loop, let in-flight infer finish
    _shutdown = True
    heartbeat_task.cancel()
    metrics_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass
    try:
        await metrics_task
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
        answer: str = r.json().get("response", "")
        latency_ms = (time.time() - start) * 1000
        _latency_window.append(latency_ms)
        _completed_tasks += 1
        return InferResponse(
            request_id=body.request_id,
            answer=answer,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        _failed_tasks += 1
        logger.error("[worker] infer failed for %s: %s", body.request_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        _active_tasks -= 1
