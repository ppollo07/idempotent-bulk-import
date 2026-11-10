"""HTTP entry point: logging, the connection pool, the routes and the response shape."""

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, Request

from . import db
from .import_service import RecordResult, import_clients, import_patients
from .schemas import BulkResponse, BulkSummary, ClientsRequest, PatientsRequest, RecordResultOut


def configure_logging() -> None:
    """Log from this package to stdout, at LOG_LEVEL, without touching the root logger.

    Uvicorn configures its own loggers; a library or a test that imports this app must not get
    its logging reconfigured as a side effect.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)-8s %(name)s %(message)s"))
    package_logger = logging.getLogger("bulk_import")
    package_logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    if not package_logger.handlers:
        package_logger.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    pool = db.create_pool(db.database_url())
    pool.open(wait=True)
    db.apply_schema(pool)
    app.state.pool = pool
    try:
        yield
    finally:
        pool.close()


app = FastAPI(title="Bulk import", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/clients/bulk")
def bulk_clients(request: Request, body: ClientsRequest) -> BulkResponse:
    return _response(import_clients(request.app.state.pool, body.records))


@app.post("/patients/bulk")
def bulk_patients(request: Request, body: PatientsRequest) -> BulkResponse:
    return _response(import_patients(request.app.state.pool, body.records))


def _response(results: list[RecordResult]) -> BulkResponse:
    counts: dict[str, int] = {"created": 0, "already_existed": 0, "conflict": 0, "failed": 0}
    for result in results:
        counts[result.status] += 1
    return BulkResponse(
        summary=BulkSummary(**counts),
        results=[RecordResultOut(**asdict(result)) for result in results],
    )
