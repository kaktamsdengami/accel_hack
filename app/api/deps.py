"""Minimal service clients and mock-mode helpers for the API.

Kept local on purpose (T10 part 2 adds app/stores.py; these helpers can be swapped for it later).
Nothing here logs connection strings or passwords.
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
from functools import lru_cache

import clickhouse_connect
import psycopg
import redis
import redis.asyncio as aredis
from fastapi import HTTPException

from app.config import ROOT, settings

MOCK_FILE = ROOT / "docs" / "api-mock.json"
CH_DB = "sponsorpulse"

# Tiny JPEG (SOI + EOI-terminated), served as evidence in mock mode.
MOCK_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////"
    "////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgB"
    "AQABPxA/2Q=="
)


def mock_enabled() -> bool:
    return os.getenv("API_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}


def mock_tick_s() -> float:
    """Seconds between simulated SSE stats events in mock mode."""
    return float(os.getenv("API_MOCK_TICK_S", "1.0"))


@lru_cache
def mock_data() -> dict:
    raw = json.loads(pathlib.Path(MOCK_FILE).read_text(encoding="utf-8"))
    return {
        "matches": raw["GET /matches"],
        "report": raw["GET /matches/m-demo-1/report"],
        "decisions": raw["GET /matches/m-demo-1/decisions?limit=3"],
        "brand": raw["GET /matches/m-demo-1/brands/b-bravo"],
        "coverage": raw["GET /matches/m-demo-1/coverage"],
    }


def not_found(what: str, ident: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} '{ident}' not found")


def unavailable(service: str) -> HTTPException:
    return HTTPException(status_code=503, detail=f"{service} is unavailable or not configured")


@lru_cache
def valkey() -> redis.Redis:
    uri = settings().valkey_uri
    if not uri:
        raise unavailable("Valkey")
    return redis.Redis.from_url(uri, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)


def valkey_async() -> aredis.Redis:
    uri = settings().valkey_uri
    if not uri:
        raise unavailable("Valkey")
    return aredis.Redis.from_url(uri, decode_responses=True, socket_connect_timeout=5)


@lru_cache
def clickhouse():
    s = settings()
    if not s.clickhouse_host:
        raise unavailable("ClickHouse")
    return clickhouse_connect.get_client(
        host=s.clickhouse_host, port=s.clickhouse_port or 8443, username=s.clickhouse_user,
        password=s.clickhouse_password, secure=True, database=CH_DB, connect_timeout=10,
    )


def pg_connect() -> psycopg.Connection:
    if not settings().pg_uri:
        raise unavailable("PostgreSQL")
    return psycopg.connect(settings().pg_uri, connect_timeout=10)


def ch_rows(sql: str, params: dict) -> list[dict]:
    """Run a parameterised query, return rows as dicts."""
    try:
        res = clickhouse().query(sql, parameters=params)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - surface as 503 without leaking credentials
        raise unavailable("ClickHouse") from e
    return [dict(zip(res.column_names, row, strict=True)) for row in res.result_rows]
