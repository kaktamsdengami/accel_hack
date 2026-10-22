"""FastAPI entrypoint. Endpoints beyond /health are added in T11 (docs/CONTRACTS.md §6)."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.contracts import CONTRACTS_VERSION

app = FastAPI(title="SponsorPulse", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=settings().cors_origins, allow_methods=["*"], allow_headers=["*"]
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "contracts": CONTRACTS_VERSION}
