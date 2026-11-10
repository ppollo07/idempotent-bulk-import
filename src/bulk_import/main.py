"""HTTP entry point for the bulk import service."""

from fastapi import FastAPI

app = FastAPI(title="Bulk import", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
