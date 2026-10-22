"""Aggregation worker (T10 part 2): Kafka -> ClickHouse + Valkey. Run: python -m workers aggregate

Consumes `detections`, `decisions` and `frames` (frames only feed the coverage ledger) in consumer group
`aggregate`. Per message batch it inserts raw rows into ClickHouse `detections` / `decisions`. About once per
second, for every match that received data since the last tick, it recomputes intervals, per-brand and
per-surface stats and the coverage ledger from ALL of that match's data so far (pure maths in
workers/aggregate_logic.py), upserts ClickHouse `intervals` / `coverage` (ReplacingMergeTree: only changed
rows are written, vanished intervals are deleted), writes the live keys of db/valkey_keys.md and
publishes the MatchReport JSON on the Valkey channel `sp:live:<match_id>`.

Inputs outside Kafka: Match metadata from the Valkey hash `sp:match:<id>`; brand names and contract targets
from Postgres `brands` / `contracts`. Recompute cost grows with the match (a full 90 min match is ~50k
detections, a few tens of ms per tick), fine for the hackathon; revisit if ticks exceed 1 s.
"""
from __future__ import annotations

import argparse
import logging
import threading
import time
from datetime import UTC, datetime

from pydantic import ValidationError

from app import stores
from app.config import settings
from app.contracts import (
    DecisionLog,
    Detection,
    Frame,
    Interval,
    Match,
    MatchReport,
    Source,
)
from workers.aggregate_logic import build_report, contract_targets

log = logging.getLogger("aggregate")

GROUP = "aggregate"
TOPICS = ("detections", "decisions", "frames")
TICK_S = 1.0
META_REFRESH_S = 30.0
STATS_TTL_S = 3600
MATCH_TTL_S = 7 * 24 * 3600
DECISIONS_KEEP = 200
ALERTS_KEEP = 100

DETECTION_COLUMNS = [
    "detection_id", "match_id", "frame_id", "shot_id", "t", "bbox_x", "bbox_y", "bbox_w", "bbox_h",
    "surface_type", "side", "board_id", "brand_id", "confidence", "reader", "area_pct", "center_dist",
    "sharpness", "occluded", "below_threshold"]
DECISION_COLUMNS = [
    "decision_id", "match_id", "shot_id", "group_id", "t", "readers_tried", "chosen_reader", "confidence",
    "cost_usd", "latency_ms", "reason", "budget_left_usd"]
INTERVAL_COLUMNS = [
    "match_id", "brand_id", "surface_type", "side", "board_id", "t_start", "t_end", "duration_s",
    "avg_area_pct", "max_area_pct", "avg_center_dist", "is_closeup", "is_replay"]
COVERAGE_COLUMNS = ["match_id", "second", "status", "reason"]


# --- row builders (pure) ----------------------------------------------------------

def detection_row(d: Detection) -> list:
    b = d.bbox
    return [d.detection_id, d.match_id, d.frame_id, d.shot_id, d.t, b.x, b.y, b.w, b.h, d.surface_type,
            d.side, d.board_id, d.brand_id, d.confidence, d.reader, d.area_pct, d.center_dist, d.sharpness,
            d.occluded, d.below_threshold]


def decision_row(d: DecisionLog) -> list:
    return [d.decision_id, d.match_id, d.shot_id, d.group_id, d.t, [str(r) for r in d.readers_tried],
            d.chosen_reader, d.confidence, d.cost_usd, d.latency_ms, d.reason, d.budget_left_usd]


def interval_key(i: Interval) -> tuple:
    return (i.brand_id, i.surface_type, i.side, i.board_id or "", i.t_start)


def interval_row(i: Interval) -> list:
    return [i.match_id, i.brand_id, i.surface_type, i.side, i.board_id or "", i.t_start, i.t_end,
            i.duration_s,
            i.avg_area_pct, i.max_area_pct, i.avg_center_dist, i.is_closeup, i.is_replay]


def surface_field(surface_type: str, side: str, board_id: str | None) -> str:
    """Field name of the `sp:{match}:surface:{brand}` hash."""
    return f"{surface_type}|{side}|{board_id or '-'}"


# --- per-match state --------------------------------------------------------------

class MatchState:
    """Everything seen for one match, keyed by id so replayed Kafka messages cannot double count."""

    def __init__(self, match_id: str):
        self.match_id = match_id
        self.detections: dict[str, Detection] = {}
        self.decisions: dict[str, DecisionLog] = {}
        self.frame_t: set[float] = set()
        self.dirty = True
        self.match: Match | None = None
        self.names: dict[str, str] = {}
        self.targets: dict[str, float] = {}
        self.meta_at = 0.0
        self.written_intervals: dict[tuple, list] = {}
        self.written_coverage: dict[int, tuple] = {}
        self.seen_alerts: set[str] = set()


class Aggregator:
    """Holds the state and talks to the stores. Injectable clients keep it testable."""

    def __init__(self, clickhouse=None, valkey=None, postgres=None, match_filter: set[str] | None = None):
        self.ch = clickhouse or stores.clickhouse()
        self.r = valkey or stores.valkey()
        self.pg = postgres or stores.postgres()
        self.cfg = settings()
        self.match_filter = match_filter
        self.states: dict[str, MatchState] = {}

    # ---- ingest ----
    def _state(self, match_id: str) -> MatchState:
        if match_id not in self.states:
            self.states[match_id] = MatchState(match_id)
        return self.states[match_id]

    def ingest(self, topic: str, payload: dict):
        """Parse one Kafka message into state. Returns (kind, model) if new detection/decision, else None."""
        try:
            cls = {"detections": Detection, "decisions": DecisionLog, "frames": Frame}[topic]
            model = cls.model_validate(
                payload)
        except (ValidationError, KeyError) as e:
            log.warning("skipping bad %s message: %s", topic, str(e)[:160])
            return None
        if self.match_filter is not None and model.match_id not in self.match_filter:
            return None
        st = self._state(model.match_id)
        st.dirty = True
        if topic == "frames":
            st.frame_t.add(model.t)
            return None
        if topic == "detections":
            fresh = model.detection_id not in st.detections
            st.detections[model.detection_id] = model
            return ("detections", model) if fresh else None
        fresh = model.decision_id not in st.decisions
        st.decisions[model.decision_id] = model
        return ("decisions", model) if fresh else None

    def store_raw(self, fresh: list) -> None:
        """Insert raw rows into ClickHouse, push decision events to Valkey. Raises if ClickHouse fails."""
        det = [detection_row(m) for k, m in fresh if k == "detections"]
        dec = [m for k, m in fresh if k == "decisions"]
        if det:
            self.ch.insert("detections", det, column_names=DETECTION_COLUMNS)
        if dec:
            self.ch.insert("decisions", [decision_row(m) for m in dec], column_names=DECISION_COLUMNS)
        for m in sorted(dec, key=lambda x: x.t):
            try:
                key = f"sp:{m.match_id}:decisions"
                p = self.r.pipeline()
                p.lpush(key, m.model_dump_json())
                p.ltrim(key, 0, DECISIONS_KEEP - 1)
                p.expire(key, MATCH_TTL_S)
                p.execute()
            except Exception as e:  # live fan-out is best effort; ClickHouse already has the row
                log.warning("valkey decision push failed: %s", e)

    # ---- metadata ----
    def _refresh_meta(self, st: MatchState) -> None:
        if st.match is not None and time.monotonic() - st.meta_at < META_REFRESH_S:
            return
        h = self.r.hgetall(f"sp:match:{st.match_id}")
        if h:
            st.match = Match.model_validate({k: v for k, v in h.items() if v != ""})
        elif st.match is None:
            st.match = Match(match_id=st.match_id, club_id="unknown", stadium_id="unknown", title=st.match_id,
                             source=Source.RECORDED, sampled_fps=self.cfg.sampled_fps,
                             started_at=datetime.now(UTC))
            self.r.hset(f"sp:match:{st.match_id}", mapping=_match_hash(st.match))
        self.r.sadd("sp:matches", st.match_id)
        try:
            club = st.match.club_id
            where, params = ("WHERE club_id = %s", (club,)) if club != "unknown" else ("", None)
            st.names = dict(self.pg.fetch(f"SELECT brand_id, name FROM brands {where}", params))
            st.targets = contract_targets(self.pg.fetch(
                f"SELECT brand_id, surface_type, target_seconds FROM contracts {where}", params))
        except Exception as e:  # keep the last known names/targets
            log.warning("postgres refresh failed: %s", e)
        st.meta_at = time.monotonic()

    # ---- tick ----
    def tick(self) -> list[str]:
        """Recompute and publish every dirty match. Returns the match ids handled."""
        done = []
        for st in list(self.states.values()):
            if not st.dirty:
                continue
            st.dirty = False
            try:
                self._tick_match(st)
                done.append(st.match_id)
            except Exception:
                st.dirty = True  # retry next tick
                log.exception("tick failed for %s", st.match_id)
        return done

    def _tick_match(self, st: MatchState) -> None:
        t0 = time.monotonic()
        self._refresh_meta(st)
        report, intervals, ledger = build_report(
            st.match, list(st.detections.values()), list(st.decisions.values()), st.frame_t, self.cfg,
            st.targets, st.names)
        self._write_clickhouse(st, intervals, ledger)
        self._write_valkey(st, report, intervals)
        log.debug("tick %s clock=%.1f brands=%d intervals=%d in %.0f ms", st.match_id, report.clock_t,
                  len(report.brands), len(intervals), 1000 * (time.monotonic() - t0))

    def _write_clickhouse(self, st: MatchState, intervals: list[Interval], ledger) -> None:
        rows = {interval_key(i): interval_row(i) for i in intervals}
        changed = [row for k, row in rows.items() if st.written_intervals.get(k) != row]
        if changed:
            self.ch.insert("intervals", changed, column_names=INTERVAL_COLUMNS)
        stale = [k for k in st.written_intervals if k not in rows]
        if stale:  # a gap was bridged and two intervals became one: drop the absorbed row(s)
            cond = " OR ".join(
                f"(brand_id = {_q(b)} AND surface_type = {_q(s)} AND side = {_q(sd)} AND board_id = {_q(bd)}"
                f" AND t_start = {t!r})" for b, s, sd, bd, t in stale)
            self.ch.command(
                f"DELETE FROM sponsorpulse.intervals WHERE match_id = {_q(st.match_id)} AND ({cond})")
        st.written_intervals = rows

        cov = {c.second: (c.status, c.reason) for c in ledger}
        cov_changed = [[st.match_id, s, *v] for s, v in cov.items() if st.written_coverage.get(s) != v]
        if cov_changed:
            self.ch.insert("coverage", cov_changed, column_names=COVERAGE_COLUMNS)
        st.written_coverage = cov

    def _write_valkey(self, st: MatchState, report: MatchReport, intervals: list[Interval]) -> None:
        m = st.match_id
        p = self.r.pipeline()
        p.set(f"sp:{m}:stats", report.model_dump_json(), ex=STATS_TTL_S)
        p.set(f"sp:{m}:clock", report.clock_t)
        p.delete(f"sp:{m}:coverage")
        cov = {"measured_s": report.coverage.measured_seconds,
               "unmeasured_s": report.coverage.unmeasured_seconds}
        cov.update({f"unmeasured:{_v(k)}": v for k, v in report.coverage.by_reason.items()})
        p.hset(f"sp:{m}:coverage", mapping=cov)
        cost = report.cost
        budget = {"limit_usd": cost.budget_usd, "spent_usd": cost.usd_total, "left_usd": cost.budget_left_usd}
        budget.update({f"calls:{_v(k)}": v for k, v in cost.calls_by_reader.items()})
        p.delete(f"sp:{m}:budget")
        p.hset(f"sp:{m}:budget", mapping=budget)
        for b in report.brands:
            p.sadd(f"sp:{m}:brands", b.brand_id)
            p.hset(f"sp:{m}:brand:{b.brand_id}", mapping={
                "seconds": b.seconds, "closeup_seconds": b.closeup_seconds,
                "replay_seconds": b.replay_seconds,
                "avg_area_pct": b.avg_area_pct, "surfaces": b.surfaces})
            pace = {"status": _v(b.pace)}
            if b.target_seconds is not None:
                pace["target_seconds"] = b.target_seconds
            if b.projected_seconds is not None:
                pace["projected_seconds"] = b.projected_seconds
            p.delete(f"sp:{m}:pace:{b.brand_id}")
            p.hset(f"sp:{m}:pace:{b.brand_id}", mapping=pace)
        by_brand: dict[str, dict[str, float]] = {}
        for s in report.surfaces:
            by_brand.setdefault(s.brand_id, {})[surface_field(s.surface_type, s.side, s.board_id)] = s.seconds
        for brand_id, fields in by_brand.items():
            p.hset(f"sp:{m}:surface:{brand_id}", mapping=fields)
        new_alerts = [a for a in report.alerts if a.alert_id not in st.seen_alerts]
        for a in new_alerts:
            st.seen_alerts.add(a.alert_id)
            p.lpush(f"sp:{m}:alerts", a.model_dump_json())
        p.ltrim(f"sp:{m}:alerts", 0, ALERTS_KEEP - 1)
        p.publish(f"sp:live:{m}", report.model_dump_json())
        for suffix in ("clock", "brands", "coverage", "budget", "alerts"):
            p.expire(f"sp:{m}:{suffix}", MATCH_TTL_S)
        p.execute()


def _v(x):
    """Enum -> its value (dict keys may still be Enum members)."""
    return getattr(x, "value", x)


def _q(v: str) -> str:
    """ClickHouse string literal."""
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _match_hash(m: Match) -> dict[str, str]:
    """Match -> flat Valkey hash (None dropped). Shared with scripts/fake_stream.py and the API."""
    return {k: (v.isoformat() if isinstance(v, datetime) else str(v))
            for k, v in m.model_dump().items() if v is not None}


# --- main loop ----------------------------------------------------------------------

def run(stop: threading.Event | None = None, group: str = GROUP, match_filter: set[str] | None = None,
        tick_s: float = TICK_S, auto_offset_reset: str = "earliest", agg: Aggregator | None = None) -> None:
    """Consume until `stop` is set (or forever). At-least-once: commit after ClickHouse accepts rows.

    `agg` lets a caller in the same process (deploy/runtime.py) keep a handle on the state, e.g. to reset
    one match before the SYNTHETIC demo replays it."""
    stop = stop or threading.Event()
    agg = agg or Aggregator(match_filter=match_filter)
    consumer = stores.kafka_consumer(*TOPICS, group_id=group, auto_offset_reset=auto_offset_reset)
    log.info("aggregate worker started: group=%s topics=%s", group, ",".join(TOPICS))
    next_tick = time.monotonic() + tick_s
    try:
        while not stop.is_set():
            batch = consumer.poll(timeout_ms=250, max_records=500)
            fresh = []
            for tp, records in batch.items():
                for rec in records:
                    got = agg.ingest(tp.topic, rec.value)
                    if got:
                        fresh.append(got)
            if batch:
                try:
                    agg.store_raw(fresh)
                    consumer.commit()
                except Exception:
                    log.exception("raw insert failed; will be re-read after restart (rows are idempotent)")
            if time.monotonic() >= next_tick:
                agg.tick()
                next_tick = time.monotonic() + tick_s
        agg.tick()  # final snapshot
    finally:
        consumer.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m workers aggregate")
    ap.add_argument("--group", default=GROUP)
    ap.add_argument("--match", action="append", help="only aggregate this match_id (repeatable)")
    ap.add_argument("--verbose", action="store_true")
    args, _ = ap.parse_known_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run(group=args.group, match_filter=set(args.match) if args.match else None)


if __name__ == "__main__":
    main()
