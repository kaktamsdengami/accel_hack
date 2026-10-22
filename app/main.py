"""FastAPI entrypoint. Endpoints beyond /health are added in T11 (docs/CONTRACTS.md §6)."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as rest_router
from app.api.stream import router as stream_router
from app.config import settings
from app.contracts import CONTRACTS_VERSION

app = FastAPI(title="SponsorPulse", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=settings().cors_origins, allow_methods=["*"], allow_headers=["*"]
)

app.include_router(rest_router)
app.include_router(stream_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "contracts": CONTRACTS_VERSION}
