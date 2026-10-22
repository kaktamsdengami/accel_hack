"""T10 part 2 without services: report assembly, event derivation and the worker with in-memory fakes."""
from datetime import UTC, datetime
from types import SimpleNamespace

from app.contracts import (
    AlertKind,
    DecisionLog,
    Detection,
    Frame,
    Match,
    MatchReport,
    PaceStatus,
    UnmeasuredReason,
)
from workers.aggregate import Aggregator, detection_row, interval_key, surface_field
from workers.aggregate_logic import build_report, contract_targets, cost_summary, unmeasured_events

CFG = SimpleNamespace(closeup_area_pct=3.0, match_budget_usd=3.0)


def match(duration=20.0):
    return Match(match_id="m", club_id="c", stadium_id="s", title="t", source="RECORDED", duration_s=duration,
                 sampled_fps=2.0, started_at=datetime(2026, 1, 1, tzinfo=UTC))


def det(i, t, brand="b-a", shot="s1", st="led_perimeter", side="far", area=2.0, board=None):
    return Detection(detection_id=f"d{i}", match_id="m", frame_id=f"f{i}", shot_id=shot, t=t,
                     bbox={"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}, surface_type=st, side=side,
                     board_id=board,
                     brand_id=brand, confidence=0.9, reader="memory", area_pct=area, center_dist=0.3,
                     sharpness=100)


def dec(i, t, conf=0.9, reason="memory match", reader="memory", cost=0.0, left=3.0, group=None):
    return DecisionLog(decision_id=f"x{i}", match_id="m", shot_id="s1", group_id=group or f"g{i}", t=t,
                       readers_tried=[reader], chosen_reader=reader, confidence=conf, cost_usd=cost,
                       latency_ms=1, reason=reason, budget_left_usd=left)


# --- pure helpers -------------------------------------------------------------------

def test_unmeasured_events_reasons():
    ds = [dec(1, 3.0, conf=0, reason="budget_cap: limit hit"),
          dec(2, 5.0, conf=0, reason="reader_error: timeout"),
          dec(3, 7.0, conf=0, reason="who knows"), dec(4, 9.0, conf=0.9)]
    assert unmeasured_events(ds) == [(3.0, UnmeasuredReason.budget_cap), (5.0, UnmeasuredReason.reader_error),
                                     (7.0, UnmeasuredReason.low_confidence)]


def test_contract_targets_brand_level_wins_else_sum_of_surfaces():
    rows = [("b-a", None, 30), ("b-a", "shirt", 5), ("b-b", "shirt", 5), ("b-b", "banner", 7)]
    assert contract_targets(rows) == {"b-a": 30.0, "b-b": 12.0}


def test_cost_summary_hand_numbers():
    ds = [dec(1, 0, cost=0.0, left=3.0, group="g1"),
          dec(2, 2, reader="claude", cost=0.012, left=2.988, group="g2"),
          dec(3, 4, reader="claude", cost=0.012, left=2.976, group="g3"),
          dec(4, 5, cost=0.0, left=2.976, group="g3")]
    c = cost_summary(ds, clock_t=60.0, budget_usd=3.0)
    assert c.usd_total == 0.024 and c.usd_per_match_minute == 0.024
    # last decision per group: g1 memory, g2 claude, g3 memory -> 1 of 3
    assert c.claude_call_share == 0.333 and dict(c.calls_by_reader) == {"memory": 2, "claude": 1}
    assert c.budget_left_usd == 2.976 and c.budget_usd == 3.0
    empty = cost_summary([], 0.0, 3.0)
    assert empty.usd_total == 0 and empty.budget_left_usd == 3.0


# --- build_report -------------------------------------------------------------------

def test_build_report_hand_calculation():
    # b-a on the far LED at t=0,0.5,1.0 (-> [0, 1.5)); frame at t=2.0 with a failed read; target 12 s of 20 s
    dets = [det(1, 0.0), det(2, 0.5), det(3, 1.0)]
    decs = [dec(1, 0.0), dec(2, 2.0, conf=0, reason="low_confidence")]
    report, intervals, ledger = build_report(match(), dets, decs, [0.0, 0.5, 1.0, 1.5, 2.0], CFG,
                                             {"b-a": 12.0}, {"b-a": "Alpha"})
    MatchReport.model_validate_json(report.model_dump_json())
    assert report.clock_t == 2.5                      # last sample 2.0 + 0.5
    assert [(i.t_start, i.t_end) for i in intervals] == [(0.0, 1.5)]
    b = report.brands[0]
    assert (b.brand_name, b.seconds, b.pct_of_match) == ("Alpha", 1.5, 7.5)   # 1.5 / 20 s
    assert b.projected_seconds == 12.0 and b.pace == PaceStatus.on_track      # 1.5 / 2.5 * 20 = 12 vs 12
    assert report.surfaces[0].surface_label == "LED perimeter · far"
    assert len(ledger) == 3                           # ceil(2.5) elapsed seconds
    assert report.coverage.measured_seconds == 2 and report.coverage.unmeasured_seconds == 1
    assert report.coverage.by_reason == {"low_confidence": 1.0}
    assert report.cost.usd_total == 0.0 and report.alerts == []


def test_build_report_alert_when_behind_and_no_pace_without_duration():
    dets = [det(i, i * 0.5) for i in range(4)]            # 2.0 s credited in the first 2 s
    behind, _, _ = build_report(match(20.0), dets, [], [], CFG, {"b-a": 100.0}, {"b-a": "Alpha"})
    assert behind.brands[0].pace == PaceStatus.behind
    assert [(a.kind, a.brand_id) for a in behind.alerts] == [(AlertKind.under_pace, "b-a")]
    assert behind.alerts[0].alert_id == "a-m-under_pace-b-a"     # stable id across ticks
    unknown, _, _ = build_report(match(None), dets, [], [], CFG, {"b-a": 100.0}, {})
    assert unknown.brands[0].pace == PaceStatus.no_target and unknown.alerts == []
    assert unknown.brands[0].pct_of_match == 100.0               # relative to the elapsed clock


def test_build_report_empty_match():
    report, intervals, ledger = build_report(match(), [], [], [], CFG)
    assert report.clock_t == 0 and not intervals and not ledger and report.brands == []


def test_budget_alert():
    report, _, _ = build_report(match(), [det(1, 0.0)], [dec(1, 0.0, left=0.0)], [], CFG)
    assert [a.kind for a in report.alerts] == [AlertKind.budget]


# --- worker with in-memory fakes ----------------------------------------------------

class FakeCH:
    def __init__(self):
        self.inserts, self.commands = [], []

    def insert(self, table, rows, column_names):
        assert all(len(r) == len(column_names) for r in rows)
        self.inserts.append((table, [list(r) for r in rows]))

    def command(self, sql):
        self.commands.append(sql)


class FakePipe:
    def __init__(self, r):
        self.r = r
        self.ops = []

    def __getattr__(self, name):
        def call(*a, **k):
            self.ops.append((name, a, k))
            return self
        return call

    def execute(self):
        self.r.calls.extend(self.ops)
        self.ops = []


class FakeRedis:
    def __init__(self, match_hash=None):
        self.calls, self.hashes, self.sets = [], {}, {}
        self.match_hash = match_hash

    def hgetall(self, key):
        return self.match_hash or {}

    def hset(self, key, mapping):
        self.hashes[key] = mapping

    def sadd(self, key, v):
        self.sets.setdefault(key, set()).add(v)

    def pipeline(self):
        return FakePipe(self)

    def published(self):
        return [(a[0], a[1]) for n, a, _ in self.calls if n == "publish"]


class FakePG:
    def fetch(self, sql, params=None):
        if "FROM brands" in sql:
            return [("b-a", "Alpha")]
        return [("b-a", None, 100.0)]


def make_agg(match_hash=None):
    ch, r = FakeCH(), FakeRedis(match_hash)
    return Aggregator(clickhouse=ch, valkey=r, postgres=FakePG()), ch, r


def feed(agg, topic, model):
    got = agg.ingest(topic, model.model_dump(mode="json"))
    return [got] if got else []


def test_ingest_dedupes_and_skips_garbage():
    agg, _, _ = make_agg()
    d = det(1, 0.0)
    assert feed(agg, "detections", d) and not feed(agg, "detections", d)   # replayed message: no new row
    assert agg.ingest("detections", {"nonsense": 1}) is None
    assert len(agg.states["m"].detections) == 1
    assert feed(agg, "frames", Frame(frame_id="f", match_id="m", shot_id="s1", group_id="g", t=1.0,
                                     width=1, height=1)) == []
    assert agg.states["m"].frame_t == {1.0}


def test_match_filter():
    agg = Aggregator(clickhouse=FakeCH(), valkey=FakeRedis(), postgres=FakePG(), match_filter={"other"})
    assert agg.ingest("detections", det(1, 0).model_dump(mode="json")) is None and not agg.states


def test_store_raw_and_tick_write_everything():
    agg, ch, r = make_agg()
    fresh = []
    for i in range(4):
        fresh += feed(agg, "detections", det(i, i * 0.5))
    fresh += feed(agg, "decisions", dec(1, 0.0))
    agg.store_raw(fresh)
    assert [t for t, _ in ch.inserts] == ["detections", "decisions"]
    assert ch.inserts[0][1][0] == detection_row(det(0, 0.0))
    assert agg.tick() == ["m"]
    tables = {t: rows for t, rows in ch.inserts}
    assert len(tables["intervals"]) == 1 and tables["intervals"][0][1] == "b-a"
    assert len(tables["coverage"]) == 2                       # clock 2.0 s -> seconds 0 and 1
    chan = {c for c, _ in r.published()}
    assert chan == {"sp:live:m"}
    stats = next(a[1] for n, a, _ in r.calls if n == "set" and a[0] == "sp:m:stats")
    report = MatchReport.model_validate_json(stats)
    assert report.brands[0].brand_name == "Alpha" and report.match.match_id == "m"
    # default match metadata was registered since none existed
    assert r.hashes["sp:match:m"]["source"] == "RECORDED" and "m" in r.sets["sp:matches"]
    assert agg.tick() == []                                   # nothing new -> nothing recomputed


def test_second_tick_writes_only_changes_and_deletes_absorbed_intervals():
    agg, ch, r = make_agg()
    for d in (det(0, 0.0), det(1, 0.5), det(2, 3.0), det(3, 3.5)):     # two runs: [0,1) and [3,4)
        feed(agg, "detections", d)
    agg.tick()
    first = next(rows for t, rows in ch.inserts if t == "intervals")
    assert len(first) == 2 and len(agg.states["m"].written_intervals) == 2
    ch.inserts.clear()
    for d in (det(4, 1.5), det(5, 2.0), det(6, 2.5)):                  # bridge the gap -> one run [0,4)
        feed(agg, "detections", d)
    agg.tick()
    rows = next(r_ for t, r_ in ch.inserts if t == "intervals")
    assert [(x[5], x[6]) for x in rows] == [(0.0, 4.0)]
    assert len(ch.commands) == 1 and "DELETE FROM sponsorpulse.intervals" in ch.commands[0]
    assert "t_start = 3.0" in ch.commands[0] and "t_start = 0.0" not in ch.commands[0]
    assert set(agg.states["m"].written_intervals) == {("b-a", "led_perimeter", "far", "", 0.0)}


def test_alert_published_once():
    agg, _, r = make_agg({"match_id": "m", "club_id": "c", "stadium_id": "s", "title": "t", "source": "LIVE",
                          "duration_s": "20.0", "sampled_fps": "2.0",
                          "started_at": "2026-01-01T00:00:00+00:00"})
    for i in range(4):
        feed(agg, "detections", det(i, i * 0.5))
    agg.tick()
    assert agg.states["m"].match.source == "LIVE"
    n_alerts = lambda: sum(1 for n, a, _ in r.calls if n == "lpush" and a[0] == "sp:m:alerts")  # noqa: E731
    assert n_alerts() == 1
    feed(agg, "detections", det(9, 2.0))
    agg.tick()
    assert n_alerts() == 1                                              # same alert id: not repeated


def test_keys_and_fields():
    assert surface_field("led_perimeter", "far", None) == "led_perimeter|far|-"
    assert surface_field("static_board", "near", "A") == "static_board|near|A"
    assert interval_key(report_interval()) == ("b-a", "led_perimeter", "far", "", 0.0)


def report_interval():
    _, intervals, _ = build_report(match(), [det(1, 0.0)], [], [], CFG)
    return intervals[0]
