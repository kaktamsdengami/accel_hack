"""SSE endpoint: GET /matches/{id}/stream.

Design choices (live mode):
- `stats` events: subscribe to Valkey pub/sub channel `sp:live:<match_id>` (payload = MatchReport JSON,
  published by the aggregation worker). The current snapshot `sp:<id>:stats` is sent once on connect.
- `decision` events: poll the Valkey list `sp:<id>:decisions` (newest first, JSON DecisionLog) every
  second and emit entries not seen before (oldest first). ClickHouse is not polled by the stream.
- A `: keep-alive` comment is sent every 15 s of silence.
- Optional `?max_events=N` closes the stream after N events (handy for curl and tests).
Mock mode simulates the clock: each tick sends a `stats` event with clock_t advanced by 1 s, and a
`decision` event every third tick.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.api import deps
from app.api.routes import stats_key
from app.contracts import DecisionLog, MatchReport

router = APIRouter()

KEEPALIVE_S = 15.0
DECISION_POLL_S = 1.0


def sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


async def mock_events(match_id: str, tick_s: float) -> AsyncIterator[str]:
    base = deps.mock_data()["report"]
    decisions = deps.mock_data()["decisions"]
    clock = float(base["clock_t"])
    n = 0
    while True:
        rep = MatchReport.model_validate({**base, "clock_t": clock})
        yield sse("stats", rep.model_dump_json())
        n += 1
        if n % 3 == 0:
            d = dict(decisions[n // 3 % len(decisions)], decision_id=f"d-sim-{n}", t=clock)
            yield sse("decision", DecisionLog.model_validate(d).model_dump_json())
        clock += 1.0
        await asyncio.sleep(tick_s)


async def live_events(match_id: str) -> AsyncIterator[str]:
    r = deps.valkey_async()
    pubsub = r.pubsub()
    seen: set[str] = set()
    last_sent = time.monotonic()
    last_poll = 0.0
    try:
        await pubsub.subscribe(f"sp:live:{match_id}")
        raw = await r.get(stats_key(match_id))
        if raw:
            yield sse("stats", MatchReport.model_validate_json(raw).model_dump_json())
            last_sent = time.monotonic()
        first = True
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg.get("type") == "message":
                try:
                    yield sse("stats", MatchReport.model_validate_json(msg["data"]).model_dump_json())
                    last_sent = time.monotonic()
                except ValueError:
                    pass  # ignore malformed snapshot
            if time.monotonic() - last_poll >= DECISION_POLL_S:
                last_poll = time.monotonic()
                items = await r.lrange(f"sp:{match_id}:decisions", 0, 49)
                fresh = []
                for item in items:
                    try:
                        d = DecisionLog.model_validate_json(item)
                    except ValueError:
                        continue
                    if d.decision_id not in seen:
                        seen.add(d.decision_id)
                        fresh.append(d)
                if not first:  # on connect, only mark existing decisions as seen
                    for d in reversed(fresh):
                        yield sse("decision", d.model_dump_json())
                        last_sent = time.monotonic()
                first = False
            if time.monotonic() - last_sent >= KEEPALIVE_S:
                yield ": keep-alive\n\n"
                last_sent = time.monotonic()
    finally:
        try:
            await pubsub.aclose()
            await r.aclose()
        except Exception:  # noqa: BLE001
            pass


async def limited(gen: AsyncIterator[str], max_events: int | None) -> AsyncIterator[str]:
    sent = 0
    try:
        async for chunk in gen:
            yield chunk
            if chunk.startswith("event:"):
                sent += 1
                if max_events and sent >= max_events:
                    return
    finally:
        await gen.aclose()


@router.get("/matches/{match_id}/stream")
async def stream(match_id: str, max_events: int | None = Query(None, ge=1)):
    if deps.mock_enabled():
        if match_id != deps.mock_data()["report"]["match"]["match_id"]:
            raise deps.not_found("Match", match_id)
        gen = mock_events(match_id, deps.mock_tick_s())
    else:
        deps.valkey()  # fail fast with 503 if Valkey is not configured; no snapshot yet is fine
        gen = live_events(match_id)
    return StreamingResponse(
        limited(gen, max_events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


