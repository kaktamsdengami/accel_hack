"""Router worker (T14): frames -> surface detection -> cheapest sufficient reader -> detections + decisions.

Run: python -m workers router [--readers stub] [--group router] [--match m-x] [--budget 3]

Consumes `frames` (consumer group `router`), collects frames by `group_id` and reads each group ONCE when it
is complete (the next group of the same match starts, or no frame arrived for GROUP_IDLE_S). Per group:
1. the SurfaceDetector finds ad surfaces on every frame (stub until T07);
2. agents.router_policy.route_group identifies the brands on the representative frame's boxes
   (memory -> Claude -> GPT, budget guarded, per-stadium thresholds from Postgres `router_thresholds`);
3. every frame's boxes become `Detection`s (other frames inherit the brand of the same surface key),
   the group's `DecisionLog` goes to `decisions`; both on Kafka (key = match_id);
4. every model call is logged to ClickHouse `agent_traces` (tokens, cost, latency, per-box output as JSON:
   the input of learn_thresholds in T15); spend is tracked in Valkey `sp:<match>:router`.

Readers: `--readers stub` (default until T07/T08/T09 land) uses the deterministic SYNTHETIC fixtures of
vision/readers.py; their costs are assumed prices, not measured spend. Replace `build_readers` to plug in
real readers; nothing else changes.

Ids are deterministic (`<group_id>:router`, `<box_id>:det`) so a replayed Kafka message overwrites, not
doubles (ClickHouse ReplacingMergeTree, aggregation dedupes by id).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field

from pydantic import ValidationError

from agents.router_policy import Budget, RouteOutcome, Thresholds, load_thresholds, route_group
from app import stores
from app.config import settings
from app.contracts import DecisionLog, Detection, Frame, Reader
from vision.readers import (
    SYNTH_BRANDS,
    Brand,
    BrandReader,
    DetectedSurface,
    FrameGroup,
    ReaderResult,
    StubClaudeReader,
    StubMemoryReader,
    StubSurfaceDetector,
    SurfaceDetector,
    surface_key,
    synthetic_claude,
    synthetic_memory,
)

log = logging.getLogger("router")

GROUP = "router"
GROUP_IDLE_S = 1.5
META_TTL_S = 60.0
THRESHOLDS_TTL_S = 300.0
MATCH_TTL_S = 7 * 24 * 3600
TRACE_COLUMNS = ["match_id", "agent", "step", "input_summary", "output_summary", "model", "tokens_in",
                 "tokens_out", "cost_usd", "latency_ms"]


# --- budget in Valkey -------------------------------------------------------------------------------------

class ValkeyBudget:
    """Per-match spend in hash `sp:<match>:router` (limit_usd, spent_usd, calls:<reader>, blocked).

    reserve() is atomic across router processes: HINCRBYFLOAT first, roll back when it overshoots the limit.
    (The aggregation worker owns `sp:<match>:budget`, which it rebuilds from DecisionLogs.)"""

    def __init__(self, r, match_id: str, limit_usd: float):
        self.r, self.key, self.limit_usd = r, f"sp:{match_id}:router", limit_usd
        if r.hsetnx(self.key, "limit_usd", limit_usd):
            r.expire(self.key, MATCH_TTL_S)
        self.limit_usd = float(r.hget(self.key, "limit_usd") or limit_usd)

    def spent(self) -> float:
        return float(self.r.hget(self.key, "spent_usd") or 0.0)

    def left(self) -> float:
        return round(self.limit_usd - self.spent(), 6)

    def reserve(self, usd: float) -> bool:
        if usd <= 0:
            return True
        if float(self.r.hincrbyfloat(self.key, "spent_usd", usd)) > self.limit_usd + 1e-9:
            self.r.hincrbyfloat(self.key, "spent_usd", -usd)
            return False
        return True

    def settle(self, reserved: float, actual: float) -> None:
        if actual != reserved:
            self.r.hincrbyfloat(self.key, "spent_usd", actual - reserved)

    def record(self, outcome: RouteOutcome) -> None:
        p = self.r.pipeline()
        for c in outcome.calls:
            p.hincrby(self.key, f"calls:{c.reader}", 1)
        if outcome.unmeasured_reason == "budget_cap":
            p.hincrby(self.key, "blocked", 1)
        p.execute()


# --- pure helpers -----------------------------------------------------------------------------------------

def readers_stub() -> dict[Reader, BrandReader]:
    return {Reader.memory: StubMemoryReader(synthetic_memory),
            Reader.claude: StubClaudeReader(synthetic_claude)}


def build_readers(kind: str = "stub") -> dict[Reader, BrandReader]:
    """Reader set by name. Only `stub` exists until T07/T08/T09; add `live` there."""
    if kind == "stub":
        return readers_stub()
    raise ValueError(f"unknown reader set {kind!r} (real readers arrive with T08/T09)")


def detections_for(group: FrameGroup, surfaces: list[DetectedSurface], outcome: RouteOutcome,
                   min_area_pct: float, min_sharpness: float) -> list[Detection]:
    """One Detection per detected box. Boxes of non-representative frames take the outcome of the
    representative box with the same surface key; without one they are published as brand unknown
    (never guessed)."""
    rep = group.representative.frame_id
    by_key = {surface_key(s.box): outcome.boxes[s.box.box_id] for s in surfaces
              if s.box.frame_id == rep and s.box.box_id in outcome.boxes}
    t_of = {f.frame_id: f.t for f in group.frames}
    out = []
    for s in surfaces:
        b = s.box
        o = outcome.boxes.get(b.box_id) or by_key.get(surface_key(b))
        area = round(b.bbox.w * b.bbox.h * 100, 4)
        cx, cy = b.bbox.x + b.bbox.w / 2, b.bbox.y + b.bbox.h / 2
        center = min(math.hypot(cx - 0.5, cy - 0.5) / math.hypot(0.5, 0.5), 1.0)
        below = area < min_area_pct or (s.sharpness is not None and s.sharpness < min_sharpness)
        out.append(Detection(
            detection_id=f"{b.box_id}:det", match_id=group.match_id, frame_id=b.frame_id,
            shot_id=group.shot_id,
            t=t_of.get(b.frame_id, group.t), bbox=b.bbox, surface_type=b.surface_type, side=b.side,
            board_id=b.board_id, brand_id=o.brand_id if o else None,
            confidence=round(o.confidence, 3) if o else 0.0, reader=o.reader if o else Reader.own_detector,
            area_pct=area, center_dist=round(center, 4), sharpness=s.sharpness or 0.0, occluded=s.occluded,
            below_threshold=below))
    return out


def trace_row(group: FrameGroup, res: ReaderResult, n_sponsors: int) -> list:
    """agent_traces row for one reader call. output_summary is JSON with per-box answers (learning input)."""
    out = {"group_id": group.group_id, "stadium_id": group.stadium_id, "error": res.error,
           "boxes": [{"box_id": b.box_id, "brand_id": b.brand_id, "confidence": round(b.confidence, 3),
                      "margin": round(b.margin, 3), "distance": b.distance, "refs": b.refs,
                      "unsure": b.unsure}
                     for b in res.boxes]}
    return [group.match_id, "router", f"read:{res.reader}",
            f"group {group.group_id} t={group.t:.1f}: {len(res.boxes)} boxes, {n_sponsors} sponsors",
            json.dumps(out)[:8000], res.model, int(res.tokens_in), int(res.tokens_out), float(res.cost_usd),
            float(res.latency_ms)]


# --- the worker -------------------------------------------------------------------------------------------

@dataclass
class _Pending:
    frames: list[Frame] = field(default_factory=list)
    last_seen: float = 0.0


class Router:
    """Holds per-match caches and talks to the stores. Every collaborator is injectable for tests."""

    def __init__(self, readers: dict[Reader, BrandReader] | None = None,
                 detector: SurfaceDetector | None = None,
                 producer=None, valkey=None, clickhouse=None, postgres=None, budget_usd: float | None = None,
                 match_filter: set[str] | None = None, budget_factory=None):
        self.cfg = settings()
        self.readers = readers if readers is not None else build_readers()
        self.detector = detector or StubSurfaceDetector()
        self.producer = producer or stores.kafka_producer()
        self.r = valkey or stores.valkey()
        self.ch = clickhouse or stores.clickhouse()
        self.pg = postgres or stores.postgres()
        self.budget_usd = budget_usd if budget_usd is not None else self.cfg.match_budget_usd
        self.match_filter = match_filter
        self.budget_factory = budget_factory or (lambda m: ValkeyBudget(self.r, m, self.budget_usd))
        self.pending: dict[tuple[str, str], _Pending] = {}
        self.open_group: dict[str, str] = {}               # match_id -> group_id being collected
        self.meta: dict[str, tuple[float, dict]] = {}        # match_id -> (fetched_at, {club, stadium})
        self.sponsors: dict[str, tuple[float, list[Brand]]] = {}
        self.thresholds: dict[str, tuple[float, Thresholds]] = {}
        self.budgets: dict[str, Budget] = {}
        self.stats = {"groups": 0, "detections": 0, "decisions": 0, "calls": 0}

    # ---- inputs ----
    def ingest(self, payload: dict, now: float | None = None) -> list[FrameGroup]:
        """Add a frame; returns groups that became complete because this frame started a new group."""
        now = time.monotonic() if now is None else now
        try:
            f = Frame.model_validate(payload)
        except ValidationError:
            log.warning("skipping invalid frame: %s", str(payload)[:200])
            return []
        if self.match_filter is not None and f.match_id not in self.match_filter:
            return []
        ready = []
        prev = self.open_group.get(f.match_id)
        if prev is not None and prev != f.group_id:
            ready += self._take(f.match_id, prev)
        self.open_group[f.match_id] = f.group_id
        p = self.pending.setdefault((f.match_id, f.group_id), _Pending())
        if all(x.frame_id != f.frame_id for x in p.frames):
            p.frames.append(f)
        p.last_seen = now
        return ready

    def idle(self, now: float | None = None, idle_s: float = GROUP_IDLE_S) -> list[FrameGroup]:
        now = time.monotonic() if now is None else now
        out = []
        for (m, g), p in list(self.pending.items()):
            if now - p.last_seen >= idle_s:
                out += self._take(m, g)
        return out

    def drain(self) -> list[FrameGroup]:
        out = []
        for m, g in list(self.pending):
            out += self._take(m, g)
        return out

    def _take(self, match_id: str, group_id: str) -> list[FrameGroup]:
        p = self.pending.pop((match_id, group_id), None)
        if self.open_group.get(match_id) == group_id:
            self.open_group.pop(match_id, None)
        if not p or not p.frames:
            return []
        frames = sorted(p.frames, key=lambda f: f.t)
        meta = self._meta(match_id)
        return [FrameGroup(match_id, group_id, frames[0].shot_id, frames, meta.get("club_id", ""),
                           meta.get("stadium_id", ""))]

    # ---- cached lookups ----
    def _meta(self, match_id: str) -> dict:
        hit = self.meta.get(match_id)
        if hit and time.monotonic() - hit[0] < META_TTL_S:
            return hit[1]
        try:
            h = self.r.hgetall(f"sp:match:{match_id}") or {}
        except Exception:
            log.exception("match meta lookup failed")
            h = {}
        self.meta[match_id] = (time.monotonic(), h)
        return h

    def _sponsors(self, club_id: str) -> list[Brand]:
        hit = self.sponsors.get(club_id)
        if hit and time.monotonic() - hit[0] < META_TTL_S:
            return hit[1]
        try:
            rows = self.pg.fetch(
                "SELECT brand_id, name, aliases FROM brands WHERE club_id = %s ORDER BY brand_id", (club_id,))
            brands = [Brand(b, n, tuple(a or ())) for b, n, a in rows]
        except Exception:
            log.exception("sponsor lookup failed")
            brands = hit[1] if hit else []
        if not brands and self.readers and all(
                getattr(r, "model", "").startswith("stub") for r in self.readers.values()):
            brands = list(SYNTH_BRANDS)   # stub readers only know the SYNTHETIC brands
        self.sponsors[club_id] = (time.monotonic(), brands)
        return brands

    def _thresholds(self, stadium_id: str) -> Thresholds:
        hit = self.thresholds.get(stadium_id)
        if hit and time.monotonic() - hit[0] < THRESHOLDS_TTL_S:
            return hit[1]
        th = load_thresholds(stadium_id, self.pg)
        self.thresholds[stadium_id] = (time.monotonic(), th)
        return th

    def _budget(self, match_id: str) -> Budget:
        if match_id not in self.budgets:
            self.budgets[match_id] = self.budget_factory(match_id)
        return self.budgets[match_id]

    # ---- processing ----
    def process(self, group: FrameGroup) -> tuple[list[Detection], DecisionLog]:
        surfaces = self.detector.detect(group)
        rep = group.representative.frame_id
        rep_boxes = [s.box for s in surfaces if s.box.frame_id == rep]
        sponsors = self._sponsors(group.club_id)
        budget = self._budget(group.match_id)
        th = self._thresholds(group.stadium_id)
        outcome = route_group(group, rep_boxes, sponsors, self.readers, th, budget)
        dets = detections_for(group, surfaces, outcome, self.cfg.min_area_pct, self.cfg.min_sharpness)
        for d in dets:
            self.producer.send("detections", key=group.match_id, value=d)
        self.producer.send("decisions", key=group.match_id, value=outcome.decision)
        if outcome.calls:
            try:
                self.ch.insert("agent_traces", [trace_row(group, c, len(sponsors)) for c in outcome.calls],
                               column_names=TRACE_COLUMNS)
            except Exception:
                log.exception("agent_traces insert failed (decision still published)")
        if isinstance(budget, ValkeyBudget):
            try:
                budget.record(outcome)
            except Exception:
                log.exception("budget counters failed")
        self.stats["groups"] += 1
        self.stats["detections"] += len(dets)
        self.stats["decisions"] += 1
        self.stats["calls"] += len(outcome.calls)
        d = outcome.decision
        log.info("%s t=%.1f %s $%.3f left=$%.3f | %s", group.group_id, group.t, d.chosen_reader, d.cost_usd,
                 d.budget_left_usd, d.reason)
        return dets, outcome.decision

    def process_all(self, groups: list[FrameGroup]) -> None:
        for g in groups:
            try:
                self.process(g)
            except Exception:
                log.exception("group %s failed", g.group_id)


def run(stop: threading.Event | None = None, group: str = GROUP, match_filter: set[str] | None = None,
        readers: dict[Reader, BrandReader] | None = None, budget_usd: float | None = None,
        auto_offset_reset: str = "earliest") -> dict[str, int]:
    """Consume `frames` until `stop` is set. Offsets are committed only when no group is half collected."""
    stop = stop or threading.Event()
    router = Router(readers=readers, budget_usd=budget_usd, match_filter=match_filter)
    consumer = stores.kafka_consumer("frames", group_id=group, auto_offset_reset=auto_offset_reset)
    log.info("router worker started: group=%s readers=%s budget=$%.2f/match", group,
             ",".join(str(r) for r in router.readers), router.budget_usd)
    try:
        while not stop.is_set():
            batch = consumer.poll(timeout_ms=250, max_records=200)
            ready = []
            for records in batch.values():
                for rec in records:
                    ready += router.ingest(rec.value)
            ready += router.idle()
            router.process_all(ready)
            if ready:
                router.producer.flush(timeout=30)
            if (batch or ready) and not router.pending:
                consumer.commit()
        router.process_all(router.drain())
        router.producer.flush(timeout=30)
        consumer.commit()
    finally:
        consumer.close()
    return router.stats


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m workers router")
    ap.add_argument("--group", default=GROUP)
    ap.add_argument("--match", action="append", help="only route this match_id (repeatable)")
    ap.add_argument("--readers", default="stub", help="reader set (only 'stub' until T07/T08/T09)")
    ap.add_argument("--budget", type=float, default=None, help="USD per match (default MATCH_BUDGET_USD)")
    ap.add_argument("--verbose", action="store_true")
    args, _ = ap.parse_known_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run(group=args.group, match_filter=set(args.match) if args.match else None,
        readers=build_readers(args.readers), budget_usd=args.budget)


if __name__ == "__main__":
    main()
