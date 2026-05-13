from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, HTTPException, Response as FastAPIResponse
from httpx import Limits
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel

from .balancer import Strategy, get_strategy
from .metrics import (
    active_requests as prom_active_requests,
    healthy_workers as prom_healthy_workers,
    request_latency,
    queue_depth,
    requests_completed,
    requests_failed,
    requests_retried,
    requests_total,
    total_workers as prom_total_workers,
    worker_active_tasks,
    worker_avg_latency_ms,
    worker_completed_tasks,
    worker_failed_tasks,
    worker_gpu_temperature_c,
    worker_gpu_utilization,
    worker_ollama_available,
    worker_resource_utilization,
    worker_status,
    worker_vram_total_gb,
    worker_vram_used_gb,
)
from .models import (
    InferRequest,
    InferResponse,
    Response,
    WorkerHeartbeat,
    WorkerMetrics,
    WorkerStatus,
)
from .rag import ChromaRetriever
from .registry import WorkerRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
LB_STRATEGY = os.environ.get("LB_STRATEGY", "least_tasks")
HEARTBEAT_INTERVAL_SECONDS = float(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "0.5"))
WORKER_MISSED_HEARTBEATS = int(os.environ.get("WORKER_MISSED_HEARTBEATS", "6"))
WORKER_STALENESS_SECONDS = float(
    os.environ.get(
        "WORKER_STALENESS_SECONDS",
        str(HEARTBEAT_INTERVAL_SECONDS * WORKER_MISSED_HEARTBEATS),
    )
)
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))
MAX_QUEUE_SIZE = int(os.environ.get("MAX_QUEUE_SIZE", "5000"))
LLM_MODEL = os.environ.get("LLM_MODEL", "llama3.1:8b")
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "3"))
KNOWLEDGE_DIR = os.environ.get("KNOWLEDGE_DIR", "/app/knowledge")
CHROMA_HOST = os.environ.get("CHROMA_HOST", "chroma")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))
CHROMA_COLLECTION = os.environ.get("CHROMA_COLLECTION", "project_knowledge")

# ── Singletons ────────────────────────────────────────────────────────────────
registry = WorkerRegistry()
retriever = ChromaRetriever(
    KNOWLEDGE_DIR,
    CHROMA_HOST,
    CHROMA_PORT,
    CHROMA_COLLECTION,
)
strategy: Strategy = get_strategy(LB_STRATEGY)
strategy_lock = asyncio.Lock()
active_request_ids: set[str] = set()
job_queue: asyncio.PriorityQueue[tuple[float, str, "QueuedJob"]] = asyncio.PriorityQueue(maxsize=MAX_QUEUE_SIZE)
_http_client: httpx.AsyncClient | None = None
_scheduler_task: asyncio.Task | None = None


@dataclass
class QueuedJob:
    request_id: str
    query: str
    prompt: str
    context: str
    rag_sources: list[str]
    rag_latency_ms: float
    created_at: float
    queued_at: float
    future: asyncio.Future[Response]


# ── Helpers ───────────────────────────────────────────────────────────────────
async def _refresh_worker_gauges() -> None:
    all_workers = await registry.get_all()
    n_healthy = sum(
        1 for w in all_workers
        if w.status == WorkerStatus.healthy and w.backend_available
    )
    prom_healthy_workers.set(n_healthy)
    prom_total_workers.set(len(all_workers))
    for w in all_workers:
        wid = w.worker_id
        worker_status.labels(worker_id=wid).set(
            1 if w.status == WorkerStatus.healthy and w.backend_available else 0
        )
        worker_ollama_available.labels(worker_id=wid).set(1 if w.backend_available else 0)
        if w.last_metrics:
            m = w.last_metrics
            worker_active_tasks.labels(worker_id=wid).set(m.active_tasks)
            worker_resource_utilization.labels(worker_id=wid).set(m.resource_utilization)
            worker_avg_latency_ms.labels(worker_id=wid).set(m.avg_latency_ms)
            worker_completed_tasks.labels(worker_id=wid).set(m.completed_tasks)
            worker_failed_tasks.labels(worker_id=wid).set(m.failed_tasks)
            worker_gpu_utilization.labels(worker_id=wid).set(m.gpu_utilization)
            worker_vram_used_gb.labels(worker_id=wid).set(m.vram_used_gb)
            worker_vram_total_gb.labels(worker_id=wid).set(m.vram_total_gb)
            worker_gpu_temperature_c.labels(worker_id=wid).set(m.gpu_temperature_c)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client, _scheduler_task
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, retriever.load)
    _http_client = httpx.AsyncClient(
        timeout=None,
        limits=Limits(
            max_connections=1000,
            max_keepalive_connections=100
        )
    )
    staleness_task = asyncio.create_task(_staleness_checker())
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info(
        "[coordinator] started | strategy=%s | staleness=%.0fs | max_retries=%d | queue=%d",
        strategy.label, WORKER_STALENESS_SECONDS, MAX_RETRIES, MAX_QUEUE_SIZE,
    )
    yield
    if active_request_ids:
        logger.info(
            "[coordinator] shutdown: waiting for %d in-flight request(s)...",
            len(active_request_ids),
        )
        while active_request_ids:
            await asyncio.sleep(0.5)
    staleness_task.cancel()
    _scheduler_task.cancel()
    try:
        await staleness_task
    except asyncio.CancelledError:
        pass
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    await _http_client.aclose()
    logger.info("[coordinator] shutdown complete")


app = FastAPI(title="Coordinator", lifespan=lifespan)


# ── Background ────────────────────────────────────────────────────────────────
async def _staleness_checker() -> None:
    interval = max(1.0, WORKER_STALENESS_SECONDS / 2)
    while True:
        await asyncio.sleep(interval)
        await registry.check_staleness(WORKER_STALENESS_SECONDS)
        await _refresh_worker_gauges()
        queue_depth.set(job_queue.qsize())


async def _scheduler_loop() -> None:
    while True:
        _, _, job = await job_queue.get()
        queue_depth.set(job_queue.qsize())
        asyncio.create_task(_execute_job(job))


async def _execute_job(job: QueuedJob) -> None:
    failed_worker_ids: set[str] = set()

    try:
        for attempt in range(MAX_RETRIES + 1):
            workers = await registry.get_all_healthy()
            eligible = [w for w in workers if w.worker_id not in failed_worker_ids]

            if not eligible:
                if attempt == 0:
                    logger.warning("[query] %s no healthy workers available", job.request_id)
                    job.future.set_exception(
                        HTTPException(status_code=503, detail="No healthy workers available")
                    )
                    return
                break

            async with strategy_lock:
                selected_strategy = strategy
                worker = selected_strategy.select(eligible)
                strategy_label = selected_strategy.label

            logger.info(
                "[query] %s dispatched to %s via %s (metric=%s) attempt=%d/%d",
                job.request_id,
                worker.worker_id,
                strategy_label,
                _metric_str(worker, strategy_label),
                attempt,
                MAX_RETRIES,
            )

            active_request_ids.add(job.request_id)

            await registry.increment_assigned(worker.worker_id)
            try:
                dispatch_started_at = time.time()
                resp = await _http_client.post(
                    f"{worker.address}/infer",
                    json=InferRequest(
                        request_id=job.request_id,
                        prompt=job.prompt,
                        model=LLM_MODEL,
                    ).model_dump(),
                )
                if resp.status_code == 200:
                    infer = InferResponse(**resp.json())
                    worker_roundtrip_ms = (time.time() - dispatch_started_at) * 1000
                    latency_ms = (time.time() - job.created_at) * 1000
                    queue_wait_ms = (dispatch_started_at - job.queued_at) * 1000
                    active_request_ids.discard(job.request_id)
                    requests_completed.inc()
                    request_latency.observe(latency_ms)
                    logger.info(
                        "[query] %s completed in %.0fms (rag=%.0fms queue=%.0fms worker=%.0fms ollama=%.0fms retries=%d) worker=%s",
                        job.request_id,
                        latency_ms,
                        job.rag_latency_ms,
                        queue_wait_ms,
                        worker_roundtrip_ms,
                        infer.ollama_latency_ms,
                        attempt,
                        worker.worker_id,
                    )
                    job.future.set_result(
                        Response(
                            request_id=job.request_id,
                            answer=infer.answer,
                            latency_ms=latency_ms,
                            worker_id=worker.worker_id,
                            retry_count=attempt,
                            rag_used=bool(job.context),
                            rag_context_chars=len(job.context),
                            rag_sources=job.rag_sources,
                            rag_latency_ms=job.rag_latency_ms,
                            queue_wait_ms=queue_wait_ms,
                            worker_latency_ms=worker_roundtrip_ms,
                            worker_queue_wait_ms=infer.worker_queue_wait_ms,
                            ollama_latency_ms=infer.ollama_latency_ms,
                            prompt_eval_count=infer.prompt_eval_count,
                            eval_count=infer.eval_count,
                            tokens_per_second=infer.tokens_per_second,
                        )
                    )
                    return

                logger.warning(
                    "[query] %s retry %d/%d — worker %s returned HTTP %d",
                    job.request_id,
                    attempt,
                    MAX_RETRIES,
                    worker.worker_id,
                    resp.status_code,
                )
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                logger.warning(
                    "[query] %s retry %d/%d — worker %s failed: %s",
                    job.request_id,
                    attempt,
                    MAX_RETRIES,
                    worker.worker_id,
                    type(exc).__name__,
                )
            finally:
                await registry.decrement_assigned(worker.worker_id)

            if attempt < MAX_RETRIES:
                requests_retried.inc()
            failed_worker_ids.add(worker.worker_id)

        active_request_ids.discard(job.request_id)
        requests_failed.inc()
        logger.error("[query] %s failed permanently after %d retries", job.request_id, MAX_RETRIES)
        job.future.set_exception(
            HTTPException(
                status_code=504,
                detail=f"Request failed after {MAX_RETRIES} retries",
            )
        )
    except Exception as exc:
        active_request_ids.discard(job.request_id)
        requests_failed.inc()
        if not job.future.done():
            job.future.set_exception(exc)


# ── Inline Pydantic bodies ────────────────────────────────────────────────────
class WorkerRegistrationBody(BaseModel):
    address: str
    name: str | None = None
    model: str | None = None
    backend_available: bool = False


class QueryBody(BaseModel):
    query: str


class StrategyBody(BaseModel):
    strategy: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/prometheus")
async def prometheus_metrics() -> FastAPIResponse:
    """Prometheus scrape endpoint — serves all coordinator + per-worker metrics."""
    await _refresh_worker_gauges()
    return FastAPIResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.post("/register")
async def register_worker(body: WorkerRegistrationBody):
    worker = await registry.register(
        address=body.address,
        name=body.name,
        model=body.model,
        backend_available=body.backend_available,
    )
    await _refresh_worker_gauges()
    return {"status": "registered", "worker_id": worker.worker_id}


@app.post("/heartbeat")
async def receive_heartbeat(heartbeat: WorkerHeartbeat):
    ok = await registry.update_heartbeat(heartbeat)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown worker: {heartbeat.worker_id}",
        )
    wid = heartbeat.worker_id
    worker_active_tasks.labels(worker_id=wid).set(heartbeat.active_tasks)
    worker_gpu_utilization.labels(worker_id=wid).set(heartbeat.gpu_utilization)
    worker_vram_used_gb.labels(worker_id=wid).set(heartbeat.vram_used_gb)
    worker_vram_total_gb.labels(worker_id=wid).set(heartbeat.vram_total_gb)
    worker_gpu_temperature_c.labels(worker_id=wid).set(heartbeat.gpu_temperature_c)
    worker_ollama_available.labels(worker_id=wid).set(1 if heartbeat.backend_available else 0)
    return {"status": "ok"}


@app.post("/metrics")
async def receive_metrics(metrics: WorkerMetrics):
    ok = await registry.update_metrics(metrics.worker_id, metrics)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown worker: {metrics.worker_id}",
        )
    wid = metrics.worker_id
    worker_active_tasks.labels(worker_id=wid).set(metrics.active_tasks)
    worker_resource_utilization.labels(worker_id=wid).set(metrics.resource_utilization)
    worker_avg_latency_ms.labels(worker_id=wid).set(metrics.avg_latency_ms)
    worker_completed_tasks.labels(worker_id=wid).set(metrics.completed_tasks)
    worker_failed_tasks.labels(worker_id=wid).set(metrics.failed_tasks)
    worker_ollama_available.labels(worker_id=wid).set(1 if metrics.backend_available else 0)
    return {"status": "received"}


@app.get("/health")
async def health():
    all_workers = await registry.get_all()
    healthy = [
        w for w in all_workers
        if w.status == WorkerStatus.healthy and w.backend_available
    ]
    return {
        "status": "ok",
        "healthy_workers": len(healthy),
        "total_workers": len(all_workers),
    }


@app.get("/workers")
async def workers():
    all_workers = await registry.get_all()
    return {"workers": all_workers}


@app.get("/debug/state")
async def debug_state():
    all_workers = await registry.get_all()
    healthy = [
        w for w in all_workers
        if w.status == WorkerStatus.healthy and w.backend_available
    ]
    return {
        "strategy": strategy.label,
        "queue_depth": job_queue.qsize(),
        "max_queue_size": MAX_QUEUE_SIZE,
        "active_requests": len(active_request_ids),
        "healthy_workers": len(healthy),
        "total_workers": len(all_workers),
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "worker_missed_heartbeats": WORKER_MISSED_HEARTBEATS,
        "worker_staleness_seconds": WORKER_STALENESS_SECONDS,
        "workers": all_workers,
    }


@app.get("/config/strategy")
async def get_active_strategy():
    return {
        "strategy": strategy.label,
        "valid_strategies": ["least_tasks", "lowest_resource", "fastest_response", "gpu_aware"],
    }


@app.post("/config/strategy")
async def set_active_strategy(body: StrategyBody):
    global strategy
    try:
        next_strategy = get_strategy(body.strategy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with strategy_lock:
        strategy = next_strategy
    logger.info("[coordinator] strategy changed to %s", strategy.label)
    return {"strategy": strategy.label}


@app.post("/query", response_model=Response)
async def handle_query(body: QueryBody):
    request_id = str(uuid.uuid4())
    created_at = time.time()

    requests_total.inc()
    prom_active_requests.inc()

    logger.info("[query] %s received: %.80s", request_id, body.query)

    loop = asyncio.get_running_loop()
    rag_started_at = time.time()
    rag_result = await loop.run_in_executor(None, retriever.retrieve, body.query, RAG_TOP_K)
    rag_latency_ms = (time.time() - rag_started_at) * 1000
    prompt = (
        f"Context:\n{rag_result.context}\n\nQuestion: {body.query}\n\nAnswer:"
        if rag_result.used
        else body.query
    )
    logger.info(
        "[query] %s enriched (rag_used=%s context=%d chars latency=%.0fms sources=%s)",
        request_id,
        rag_result.used,
        rag_result.context_chars,
        rag_latency_ms,
        ",".join(rag_result.sources) or "none",
    )

    loop = asyncio.get_running_loop()
    future: asyncio.Future[Response] = loop.create_future()
    queued_at = time.time()
    job = QueuedJob(
        request_id=request_id,
        query=body.query,
        prompt=prompt,
        context=rag_result.context,
        rag_sources=rag_result.sources,
        rag_latency_ms=rag_latency_ms,
        created_at=created_at,
        queued_at=queued_at,
        future=future,
    )

    try:
        job_queue.put_nowait((created_at, request_id, job))
        queue_depth.set(job_queue.qsize())
    except asyncio.QueueFull as exc:
        requests_failed.inc()
        prom_active_requests.dec()
        raise HTTPException(status_code=429, detail="Coordinator queue is full") from exc

    try:
        return await future
    finally:
        prom_active_requests.dec()


def _metric_str(worker, strategy_label: str) -> str:
    if worker.last_metrics is None:
        return "N/A"
    m = worker.last_metrics
    active = max(m.active_tasks, worker.assigned_tasks)
    if strategy_label == "least_tasks":
        return str(active)
    if strategy_label == "lowest_resource":
        return f"{m.resource_utilization:.2f}"
    if strategy_label == "fastest_response":
        return f"{m.avg_latency_ms:.0f}ms"
    if strategy_label == "gpu_aware":
        return (
            f"active={active},gpu={m.gpu_utilization:.2f},"
            f"vram={m.vram_used_gb:.1f}/{m.vram_total_gb:.1f}GB,"
            f"lat={m.avg_latency_ms:.0f}ms"
        )
    return "N/A"
