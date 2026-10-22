"""REST endpoints of docs/CONTRACTS.md section 6. Mock mode (API_MOCK=1) serves docs/api-mock.json."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from app.api import deps
from app.contracts import (
    BrandDetail,
    BrandStats,
    ChatAnswer,
    ChatRequest,
    CoverageSecond,
    DecisionLog,
    Interval,
    Match,
    MatchReport,
    SurfaceStats,
)

router = APIRouter()


def stats_key(match_id: str) -> str:
    return f"sp:{match_id}:stats"  # db/valkey_keys.md


def load_report(match_id: str) -> MatchReport:
    if deps.mock_enabled():
        rep = deps.mock_data()["report"]
        if match_id != rep["match"]["match_id"]:
            raise deps.not_found("Match", match_id)
        return MatchReport.model_validate(rep)
    try:
        raw = deps.valkey().get(stats_key(match_id))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise deps.unavailable("Valkey") from e
    if not raw:
        raise deps.not_found("Report for match", match_id)
    return MatchReport.model_validate_json(raw)


def list_matches_live() -> list[Match]:
    r = deps.valkey()
    out: dict[str, Match] = {}
    for key in r.scan_iter(match="sp:match:*", count=200):
        h = r.hgetall(key)
        if h:
            try:
                m = Match.model_validate(h)
            except ValueError:
                continue
            out[m.match_id] = m
    # Fallback: matches known only through their report snapshot.
    for key in r.scan_iter(match="sp:*:stats", count=200):
        mid = key.split(":")[1]
        if mid in out or mid in {"meta", "match", "live"}:
            continue
        raw = r.get(key)
        if raw:
            try:
                out[mid] = MatchReport.model_validate_json(raw).match
            except ValueError:
                continue
    return sorted(out.values(), key=lambda m: m.started_at, reverse=True)


@router.get("/matches", response_model=list[Match])
def get_matches():
    if deps.mock_enabled():
        return [Match.model_validate(m) for m in deps.mock_data()["matches"]]
    try:
        return list_matches_live()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise deps.unavailable("Valkey") from e


@router.get("/matches/{match_id}/report", response_model=MatchReport)
def get_report(match_id: str):
    return load_report(match_id)


@router.get("/matches/{match_id}/brands/{brand_id}", response_model=BrandDetail)
def get_brand(match_id: str, brand_id: str):
    rep = load_report(match_id)
    brand: BrandStats | None = next((b for b in rep.brands if b.brand_id == brand_id), None)
    if brand is None:
        raise deps.not_found("Brand", brand_id)
    surfaces: list[SurfaceStats] = [s for s in rep.surfaces if s.brand_id == brand_id]
    if deps.mock_enabled():
        mock = deps.mock_data()["brand"]
        intervals = [Interval.model_validate(i) for i in mock["intervals"] if i["brand_id"] == brand_id]
    else:
        rows = deps.ch_rows(
            "SELECT match_id, brand_id, surface_type, side, board_id, t_start, t_end, duration_s, "
            "avg_area_pct, max_area_pct, avg_center_dist, is_closeup, is_replay "
            "FROM intervals FINAL WHERE match_id = {m:String} AND brand_id = {b:String} ORDER BY t_start",
            {"m": match_id, "b": brand_id},
        )
        for row in rows:
            row["board_id"] = row["board_id"] or None  # stored as '' when there is no board
        intervals = [Interval.model_validate(r) for r in rows]
    return BrandDetail(brand=brand, surfaces=surfaces, intervals=intervals)


def fetch_decisions(match_id: str, limit: int) -> list[DecisionLog]:
    """Newest first. Shared with the SSE stream."""
    rows = deps.ch_rows(
        "SELECT decision_id, match_id, shot_id, group_id, t, readers_tried, chosen_reader, confidence, "
        "cost_usd, latency_ms, reason, budget_left_usd FROM decisions FINAL "
        "WHERE match_id = {m:String} ORDER BY t DESC, decision_id DESC LIMIT {n:UInt32}",
        {"m": match_id, "n": limit},
    )
    return [DecisionLog.model_validate(r) for r in rows]


@router.get("/matches/{match_id}/decisions", response_model=list[DecisionLog])
def get_decisions(match_id: str, limit: int = Query(50, ge=1, le=500)):
    if deps.mock_enabled():
        load_report(match_id)  # 404 for an unknown match
        return [DecisionLog.model_validate(d) for d in deps.mock_data()["decisions"][:limit]]
    return fetch_decisions(match_id, limit)


@router.get("/matches/{match_id}/coverage", response_model=list[CoverageSecond])
def get_coverage(match_id: str):
    if deps.mock_enabled():
        load_report(match_id)
        return [CoverageSecond.model_validate(c) for c in deps.mock_data()["coverage"]]
    rows = deps.ch_rows(
        "SELECT match_id, second, status, reason FROM coverage FINAL "
        "WHERE match_id = {m:String} ORDER BY second",
        {"m": match_id},
    )
    return [CoverageSecond.model_validate(r) for r in rows]


@router.get("/evidence/{frame_id}.jpg")
def get_evidence(frame_id: str):
    if deps.mock_enabled():
        if not frame_id.startswith("m-demo-1:"):
            raise deps.not_found("Evidence frame", frame_id)
        return Response(deps.MOCK_JPEG, media_type="image/jpeg")
    try:
        with deps.pg_connect() as conn:
            row = conn.execute("SELECT jpeg FROM evidence WHERE frame_id = %s", (frame_id,)).fetchone()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise deps.unavailable("PostgreSQL") from e
    if row is None:
        raise deps.not_found("Evidence frame", frame_id)
    return Response(bytes(row[0]), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


@router.post("/matches/{match_id}/chat", response_model=ChatAnswer)
def chat(match_id: str, body: ChatRequest):
    """Stub until the sponsor analyst agent (T17) is wired in."""
    if deps.mock_enabled():
        load_report(match_id)
    return ChatAnswer(
        answer="The sponsor analyst arrives in T17; chat is not available yet.",
        not_measured_note="No analysis was run for this question.",
    )


@router.post("/matches/{match_id}/replay", status_code=202)
def replay(match_id: str):
    """Admin stub: will start replaying a recorded segment as a live stream (wired with the ingest worker).

    Returns 202 and says plainly that nothing was started yet.
    """
    if deps.mock_enabled():
        load_report(match_id)
    return {"accepted": True, "started": False, "detail": "replay trigger is not wired yet (ingest worker)"}
