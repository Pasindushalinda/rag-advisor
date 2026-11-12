"""Custom routes mounted into the Aegra server (aegra.json): health check and filing ingestion.

Chat goes through Aegra's own thread/run API, not through this app: Aegra attaches its Postgres
checkpointer to the investor_advisor graph, so each thread keeps its state across follow-up turns.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4

from aegra_api.core.auth_deps import require_auth
from aegra_api.models.auth import User
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
from langfuse import Langfuse
from pydantic import ValidationError
from qdrant_client import QdrantClient, models

from auth import ADMIN_PERMISSION, INGEST_PERMISSION
from graphs.ingestion.ingestion import ingest
from shared.config import get_settings
from shared.models import IndexedAnnualFiling, IndexedFilingPayload, IngestionJob, IngestionRequest

logger = logging.getLogger(__name__)
ingestion_jobs: dict[str, IngestionJob] = {}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_settings()  # exports LANGFUSE_* from .env into os.environ before the client is built
    yield
    Langfuse().flush()


app = FastAPI(lifespan=lifespan)


def _process_ingestion(job: IngestionJob, request: IngestionRequest) -> None:
    def update_progress(progress: int) -> None:
        current_job = ingestion_jobs.get(job.id, job)
        ingestion_jobs[job.id] = current_job.model_copy(update={"progress": progress})

    try:
        ingest(request, progress_callback=update_progress)
    except Exception:
        logger.exception("Ingestion job %s failed", job.id)
        current_job = ingestion_jobs.get(job.id, job)
        ingestion_jobs[job.id] = current_job.model_copy(
            update={"status": "failed", "message": "Ingestion failed. Check API logs."}
        )
    else:
        ingestion_jobs[job.id] = job.model_copy(
            update={"status": "done", "progress": 100, "message": "Filing indexed successfully."}
        )


def _require_ingestion_access(user: User) -> None:
    if not {ADMIN_PERMISSION, INGEST_PERMISSION}.intersection(user.permissions):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "ingestion access required")


def _indexed_10k_filings(client: QdrantClient, collection_name: str) -> list[IndexedAnnualFiling]:
    if not client.collection_exists(collection_name):
        return []

    filings: dict[str, IndexedAnnualFiling] = {}
    points, next_offset = client.scroll(
        collection_name,
        scroll_filter=models.Filter(
            must=[models.FieldCondition(key="form_type", match=models.MatchValue(value="10-K"))]
        ),
        limit=256,
        with_payload=["ticker", "company", "form_type", "period_end", "file"],
    )
    while True:
        for point in points:
            if point.payload is None:
                continue
            try:
                payload = IndexedFilingPayload.model_validate(point.payload)
                year = int(payload.period_end[:4])
                if payload.form_type != "10-K":
                    continue
            except (ValidationError, ValueError):
                logger.warning("Skipping invalid filing metadata in Qdrant point %s", point.id)
                continue
            filings.setdefault(
                payload.file,
                IndexedAnnualFiling(
                    ticker=payload.ticker,
                    company=payload.company.title(),
                    year=year,
                    period_end=payload.period_end,
                    file=payload.file,
                ),
            )
        if next_offset is None:
            break
        points, next_offset = client.scroll(
            collection_name,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="form_type", match=models.MatchValue(value="10-K"))]
            ),
            limit=256,
            offset=next_offset,
            with_payload=["ticker", "company", "form_type", "period_end", "file"],
        )
    return sorted(filings.values(), key=lambda filing: (-filing.year, filing.company, filing.ticker))


@app.get("/health")
def health() -> Literal["ok"]:
    return "ok"


@app.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
def create_ingestion(
    request: IngestionRequest,
    background_tasks: BackgroundTasks,
    user: Annotated[User, Depends(require_auth)],
) -> IngestionJob:
    _require_ingestion_access(user)
    job = IngestionJob(id=str(uuid4()), ticker=request.ticker, company=request.company, status="pending")
    ingestion_jobs[job.id] = job
    background_tasks.add_task(_process_ingestion, job, request)
    return job


@app.get("/ingest/filings", response_model=list[IndexedAnnualFiling])
def list_indexed_10k_filings(
    user: Annotated[User, Depends(require_auth)],
) -> list[IndexedAnnualFiling]:
    _require_ingestion_access(user)
    cfg = get_settings()
    client = QdrantClient(
        url=cfg.qdrant_url,
        port=None,
        timeout=cfg.qdrant_timeout,
        api_key=cfg.qdrant_api_key.get_secret_value() if cfg.qdrant_api_key else None,
    )
    try:
        return _indexed_10k_filings(client, cfg.qdrant_collection)
    finally:
        client.close()


@app.get("/ingest/{job_id}", response_model=IngestionJob)
def get_ingestion_status(
    job_id: str,
    user: Annotated[User, Depends(require_auth)],
) -> IngestionJob:
    _require_ingestion_access(user)
    job = ingestion_jobs.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ingestion job not found")
    return job
