from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Response as FastAPIResponse
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
    worker_resource_utilization,
    worker_status,
    worker_vram_total_gb,
    worker_vram_used_gb,
)
from .models import (
    InferRequest,
    InferResponse,
    Request,
    RequestStatus,
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
WORKER_STALENESS_SECONDS = float(os.environ.get("WORKER_STALENESS_SECONDS", "30"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))
MAX_QUEUE_SIZE = int(os.environ.get("MAX_QUEUE_SIZE", "5000"))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60"))
LLM_MODEL = os.environ.get("LLM_MODEL", "llama3.2:8b")
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "3"))
KNOWLEDGE_DIR = os.environ.get("KNOWLEDGE_DIR", "/app/knowledge")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
CHROMA_HOST = os.environ.get("CHROMA_HOST", "chroma")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))
CHROMA_COLLECTION = os.environ.get("CHROMA_COLLECTION", "project_knowledge")

# ── Singletons ────────────────────────────────────────────────────────────────
registry = WorkerRegistry()
retriever = ChromaRetriever(
    KNOWLEDGE_DIR,
    EMBEDDING_MODEL,
    CHROMA_HOST,
    CHROMA_PORT,
    CHROMA_COLLECTION,
)
strategy: Strategy = get_strategy(LB_STRATEGY)
strategy_lock = asyncio.Lock()
active_requests: dict[str, Request] = {}
job_queue: asyncio.Queue["QueuedJob"] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
_http_client: Optional[httpx.AsyncClient] = None
_scheduler_task: Optional[asyncio.Task] = None


@dataclass
class QueuedJob:
    request_id: str
    query: str
    prompt: str
    context: str
    created_at: float
    future: asyncio.Future[Response]


# ── Helpers ───────────────────────────────────────────────────────────────────
async def _refresh_worker_gauges() -> None:
    all_workers = await registry.get_all()
    n_healthy = sum(1 for w in all_workers if w.status == WorkerStatus.healthy)
    prom_healthy_workers.set(n_healthy)
    prom_total_workers.set(len(all_workers))
    for w in all_workers:
        wid = w.worker_id
        worker_status.labels(worker_id=wid).set(
            1 if w.status == WorkerStatus.healthy else 0
        )
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
    retriever.load()
    _http_client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS + 5)
    staleness_task = asyncio.create_task(_staleness_checker())
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info(
        "[coordinator] started | strategy=%s | staleness=%.0fs | max_retries=%d | queue=%d",
        strategy.label, WORKER_STALENESS_SECONDS, MAX_RETRIES, MAX_QUEUE_SIZE,
    )
    yield
    if active_requests:
        logger.info(
            "[coordinator] shutdown: waiting for %d in-flight request(s)...",
            len(active_requests),
        )
        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        while active_requests and time.monotonic() < deadline:
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
        job = await job_queue.get()
        queue_depth.set(job_queue.qsize())
        asyncio.create_task(_execute_job(job))


async def _execute_job(job: QueuedJob) -> None:
    failed_worker_ids: set[str] = set()

    try:
        for attempt in range(MAX_RETRIES + 1):
            workers = await registry.get_all_healthy(WORKER_STALENESS_SECONDS)
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

            active_requests[job.request_id] = Request(
                id=job.request_id,
                query=job.query,
                context=job.context or None,
                status=RequestStatus.in_progress,
                assigned_worker_id=worker.worker_id,
                retry_count=attempt,
                created_at=job.created_at,
                dispatched_at=time.time(),
            )

            await registry.increment_assigned(worker.worker_id)
            try:
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
                    latency_ms = (time.time() - job.created_at) * 1000
                    active_requests.pop(job.request_id, None)
                    requests_completed.inc()
                    request_latency.observe(latency_ms)
                    logger.info(
                        "[query] %s completed in %.0fms (retries=%d) worker=%s",
                        job.request_id,
                        latency_ms,
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

        active_requests.pop(job.request_id, None)
        requests_failed.inc()
        logger.error("[query] %s failed permanently after %d retries", job.request_id, MAX_RETRIES)
        job.future.set_exception(
            HTTPException(
                status_code=504,
                detail=f"Request failed after {MAX_RETRIES} retries",
            )
        )
    except Exception as exc:
        active_requests.pop(job.request_id, None)
        requests_failed.inc()
        if not job.future.done():
            job.future.set_exception(exc)


# ── Inline Pydantic bodies ────────────────────────────────────────────────────
class WorkerRegistrationBody(BaseModel):
    address: str
    name: str | None = None
    model: str | None = None


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
    return {"status": "received"}


@app.get("/health")
async def health():
    all_workers = await registry.get_all()
    healthy = [w for w in all_workers if w.status == WorkerStatus.healthy]
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
    healthy = [w for w in all_workers if w.status == WorkerStatus.healthy]
    return {
        "strategy": strategy.label,
        "queue_depth": job_queue.qsize(),
        "max_queue_size": MAX_QUEUE_SIZE,
        "active_requests": len(active_requests),
        "healthy_workers": len(healthy),
        "total_workers": len(all_workers),
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

    context = retriever.retrieve(body.query, RAG_TOP_K)
    prompt = (
        f"Context:\n{context}\n\nQuestion: {body.query}\n\nAnswer:"
        if context
        else body.query
    )
    logger.info("[query] %s enriched (context %d chars)", request_id, len(context))

    loop = asyncio.get_running_loop()
    future: asyncio.Future[Response] = loop.create_future()
    job = QueuedJob(
        request_id=request_id,
        query=body.query,
        prompt=prompt,
        context=context,
        created_at=created_at,
        future=future,
    )

    try:
        job_queue.put_nowait(job)
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
