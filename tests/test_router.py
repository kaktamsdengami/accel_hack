"""Router policy and worker shell with stub readers (no network)."""
from agents.router_policy import (
    LocalBudget,
    MemoryObservation,
    Thresholds,
    escalation_cause,
    learn_thresholds,
    load_thresholds,
    route_group,
)
from app.contracts import BBox, Frame, Reader, Side, SurfaceBox, SurfaceType
from vision.readers import (
    SYNTH_BRANDS,
    BoxRead,
    Brand,
    BrandReader,
    FrameGroup,
    StubAnswer,
    StubClaudeReader,
    StubGptReader,
    StubMemoryReader,
    StubSurfaceDetector,
    SurfaceDetector,
)
from workers import aggregate_logic
from workers.router import Router, detections_for

TH = Thresholds(memory_max_distance=0.3, memory_min_margin=0.1, min_refs=3)
SPONSORS = [Brand("b-a", "Acme"), Brand("b-b", "Bolt"), Brand("b-c", "Crest")]
LED = "led_perimeter|far|-"
BOARD = "static_board|near|A"


def group(gid="m-t:g0", t=0.0, n=1):
    frames = [Frame(frame_id=f"m-t:{int(t * 2) + i}", match_id="m-t", shot_id="m-t:s0", group_id=gid,
                    t=t + i / 2, width=1280, height=720) for i in range(n)]
    return FrameGroup("m-t", gid, "m-t:s0", frames, "c-t", "st-t")


def boxes(g):
    f = g.representative.frame_id
    return [SurfaceBox(box_id=f"{f}:led", frame_id=f, bbox=BBox(x=0.1, y=0.05, w=0.5, h=0.05),
                       surface_type=SurfaceType.led_perimeter, side=Side.far, score=0.9),
            SurfaceBox(box_id=f"{f}:boardA", frame_id=f, bbox=BBox(x=0.2, y=0.8, w=0.3, h=0.04),
                       surface_type=SurfaceType.static_board, side=Side.near, board_id="A", score=0.9)]


SURE = StubAnswer("b-a", 0.93, margin=0.4, distance=0.1, refs=8)


def run(mem_fixture, claude_fixture=None, gpt_fixture=None, budget=1.0, **kw):
    readers = {Reader.memory: StubMemoryReader(mem_fixture)}
    if claude_fixture is not None:
        readers[Reader.claude] = StubClaudeReader(claude_fixture, **kw)
    if gpt_fixture is not None:
        readers[Reader.gpt] = StubGptReader(gpt_fixture)
    g = group()
    b = LocalBudget(budget)
    return route_group(g, boxes(g), SPONSORS, readers, TH, b), readers, b


def test_stubs_satisfy_protocols():
    assert isinstance(StubMemoryReader({}), BrandReader)
    assert isinstance(StubClaudeReader({}), BrandReader)
    assert isinstance(StubSurfaceDetector(), SurfaceDetector)


def test_escalation_causes():
    ok = BoxRead("x", "b-a", 0.9, margin=0.4, distance=0.1, refs=8)
    assert escalation_cause(ok, TH, {"b-a"}) is None
    assert escalation_cause(BoxRead("x", None, 0.0), TH) == "unsure"
    assert escalation_cause(BoxRead("x", "b-a", 0.9, unsure=True), TH) == "unsure"
    assert escalation_cause(BoxRead("x", "b-a", 0.9, margin=0.4, distance=0.5, refs=8), TH) == "far"
    assert escalation_cause(BoxRead("x", "b-a", 0.9, margin=0.05, distance=0.1, refs=8), TH) == "margin"
    assert escalation_cause(BoxRead("x", "b-a", 0.9, margin=0.4, distance=0.1, refs=1), TH) == "thin"
    assert escalation_cause(ok, TH, {"b-z"}) == "off_list"


def test_confident_memory_needs_no_paid_call():
    out, readers, budget = run({LED: SURE, BOARD: StubAnswer("b-b", 0.9, 0.3, 0.15, 5)},
                               {LED: StubAnswer("b-c", 0.9)})
    d = out.decision
    assert d.chosen_reader == "memory" and d.readers_tried == ["memory"] and d.cost_usd == 0
    assert readers[Reader.claude].calls == 0 and budget.spent_usd == 0
    assert {o.brand_id for o in out.boxes.values()} == {"b-a", "b-b"}
    assert d.confidence > 0.9 and "Acme" in d.reason and "Bolt" in d.reason and "zero model cost" in d.reason


def test_unsure_memory_escalates_only_unsure_box():
    out, readers, budget = run({LED: SURE}, {BOARD: StubAnswer("b-c", 0.88)})
    d = out.decision
    assert d.readers_tried == ["memory", "claude"] and d.chosen_reader == "claude"
    assert out.causes == {f"{group().representative.frame_id}:boardA": "unsure"}
    assert out.boxes["m-t:0:led"].brand_id == "b-a" and out.boxes["m-t:0:led"].reader == "memory"
    assert out.boxes["m-t:0:boardA"].brand_id == "b-c" and out.boxes["m-t:0:boardA"].reader == "claude"
    assert d.cost_usd == 0.012 and budget.spent_usd == 0.012 and d.budget_left_usd == 0.988
    assert "Claude checked 1 surface for $0.012" in d.reason and "Crest" in d.reason


def test_small_margin_escalates_and_names_lookalike():
    close = StubAnswer("b-a", 0.6, margin=0.03, distance=0.1, refs=8, runner_up="b-b")
    out, _, _ = run({LED: close, BOARD: SURE}, {LED: StubAnswer("b-a", 0.9)})
    assert set(out.causes.values()) == {"margin"}
    assert "could not tell Acme from Bolt" in out.decision.reason and "confirmed Acme" in out.decision.reason


def test_thin_memory_escalates():
    out, _, _ = run({LED: StubAnswer("b-a", 0.9, margin=0.4, distance=0.1, refs=1), BOARD: SURE},
                    {LED: StubAnswer("b-a", 0.9)})
    assert set(out.causes.values()) == {"thin"} and out.decision.chosen_reader == "claude"
    assert "only 1 reference image of Acme" in out.decision.reason


def test_conflict_goes_to_gpt_and_majority_wins():
    close = StubAnswer("b-a", 0.6, margin=0.03, distance=0.1, refs=8, runner_up="b-b")
    out, readers, budget = run({LED: close, BOARD: SURE}, {LED: StubAnswer("b-b", 0.85)},
                               gpt_fixture={LED: StubAnswer("b-b", 0.8)})
    d = out.decision
    assert out.conflicts == ["m-t:0:led"] and d.readers_tried == ["memory", "claude", "gpt"]
    assert d.chosen_reader == "gpt" and out.boxes["m-t:0:led"].brand_id == "b-b"
    assert round(budget.spent_usd, 6) == 0.022 and "GPT broke the tie: Bolt" in d.reason


def test_conflict_without_gpt_keeps_claude_at_reduced_confidence():
    close = StubAnswer("b-a", 0.6, margin=0.03, distance=0.1, refs=8)
    out, _, _ = run({LED: close, BOARD: SURE}, {LED: StubAnswer("b-b", 0.95)})
    led = out.boxes["m-t:0:led"]
    assert led.brand_id == "b-b" and led.confidence == TH.conflict_confidence
    r = out.decision.reason
    assert "conflict" in r and "Memory said Acme but Claude said Bolt" in r


def test_budget_cap_never_calls_claude_and_marks_unmeasured():
    out, readers, budget = run({LED: SURE}, {BOARD: StubAnswer("b-c", 0.9)}, budget=0.005)
    d = out.decision
    assert readers[Reader.claude].calls == 0 and budget.spent_usd == 0
    assert d.confidence == 0 and d.reason.startswith("budget_cap") and out.unmeasured_reason == "budget_cap"
    assert out.boxes["m-t:0:led"].brand_id == "b-a"          # what memory read confidently is kept
    assert out.boxes["m-t:0:boardA"].brand_id is None        # never guessed
    # the aggregation turns this into unmeasured seconds with reason budget_cap
    assert aggregate_logic.unmeasured_events([d]) == [(0.0, "budget_cap")]


def test_budget_runs_out_mid_match():
    claude = StubClaudeReader({BOARD: StubAnswer("b-c", 0.9)})
    readers = {Reader.memory: StubMemoryReader({LED: SURE}), Reader.claude: claude}
    budget = LocalBudget(0.03)
    reasons = []
    for i in range(5):
        g = group(f"m-t:g{i}", t=2.0 * i)
        reasons.append(route_group(g, boxes(g), SPONSORS, readers, TH, budget).decision)
    assert claude.calls == 2 and round(budget.spent_usd, 6) == 0.024 and budget.left() >= 0
    assert [d.reason.startswith("budget_cap") for d in reasons] == [False, False, True, True, True]


def test_reader_error_marks_unmeasured():
    out, _, budget = run({LED: SURE}, {BOARD: StubAnswer("b-c", 0.9)}, fail_groups={"m-t:g0"})
    assert out.decision.reason.startswith("reader_error") and out.decision.confidence == 0
    assert aggregate_logic.unmeasured_events([out.decision])[0][1] == "reader_error"


def test_no_boxes_is_measured_and_free():
    g = group()
    out = route_group(g, [], SPONSORS, {Reader.memory: StubMemoryReader({})}, TH, LocalBudget(1))
    d = out.decision
    assert d.confidence == 1.0 and d.cost_usd == 0 and d.chosen_reader == "own_detector"


def test_reasons_are_one_short_sentence():
    close = StubAnswer("b-a", 0.6, margin=0.03, distance=0.1, refs=8, runner_up="b-b")
    out, _, _ = run({LED: close, BOARD: SURE}, {LED: StubAnswer("b-a", 0.9)})
    r = out.decision.reason
    assert r.endswith(".") and r.count(". ") == 0 and len(r) < 200


def _learning_fixture():
    """Memory is right whenever margin >= 0.06, at any distance up to 0.32; wrong below 0.04 margin."""
    obs, truth = [], {}
    for i in range(60):
        key = f"k{i}"
        if i % 6 == 0:   # look-alike confusions: small margin, wrong brand
            obs.append(MemoryObservation(key, "b-b", distance=0.15, margin=0.02, refs=8))
        else:
            obs.append(MemoryObservation(key, "b-a", distance=0.05 + 0.27 * (i % 10) / 9,
                                         margin=0.06 + 0.01 * (i % 5), refs=8))
        truth[key] = "b-a"
    return obs, truth


def test_learning_cuts_escalations_at_equal_accuracy():
    obs, truth = _learning_fixture()
    strict = Thresholds(memory_max_distance=0.15, memory_min_margin=0.15, min_refs=3)
    res = learn_thresholds(obs, truth, strict)
    assert res.baseline_accuracy == 1.0 and res.accuracy == 1.0
    assert res.escalation_rate < res.baseline_escalation_rate
    assert abs(res.escalation_rate - 10 / 60) < 1e-9          # only the 10 true confusions still go to Claude
    th = res.thresholds
    assert th.source == "learned" and th.memory_max_distance >= 0.32 and 0.02 < th.memory_min_margin <= 0.06


def test_learning_needs_enough_samples():
    obs, truth = _learning_fixture()
    res = learn_thresholds(obs[:5], truth, TH)
    assert res.thresholds == TH and res.samples == 5


class _PG:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail = rows or [], fail

    def fetch(self, sql, params=None):
        if self.fail:
            raise ConnectionError("down")
        return self.rows


def test_thresholds_load_per_stadium_with_default_fallback():
    th = load_thresholds("st-x", _PG([(0.4, 0.05, 2, "learned")]))
    assert (th.memory_max_distance, th.memory_min_margin, th.min_refs, th.source) == (0.4, 0.05, 2, "learned")
    assert load_thresholds("st-x", _PG()).source == "default"
    assert load_thresholds("st-x", _PG(fail=True)).source == "default"


# --- worker shell ----------------------------------------------------------------------------------------

class _Producer:
    def __init__(self):
        self.sent = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))

    def flush(self, timeout=None):
        pass


class _CH:
    def __init__(self):
        self.rows = []

    def insert(self, table, rows, column_names=None):
        self.rows += [(table, dict(zip(column_names, r, strict=True))) for r in rows]


class _R:
    def hgetall(self, key):
        return {"club_id": "c-t", "stadium_id": "st-t"}


class _BrandsPG(_PG):
    def fetch(self, sql, params=None):
        if "brands" in sql:
            return [(b.brand_id, b.name, []) for b in SYNTH_BRANDS]
        return []


def _frame(n, gid):
    return Frame(frame_id=f"m-w:{n}", match_id="m-w", shot_id="m-w:s0", group_id=gid, t=n / 2, width=1280,
                 height=720).model_dump()


def test_worker_groups_frames_and_publishes():
    prod, ch = _Producer(), _CH()
    router = Router(producer=prod, valkey=_R(), clickhouse=ch, postgres=_BrandsPG(),
                    budget_factory=lambda m: LocalBudget(3.0))
    ready = []
    for n in range(8):                      # 4 groups of 2 frames
        ready += router.ingest(_frame(n, f"m-w:g{n // 2}"), now=float(n))
    assert [g.group_id for g in ready] == ["m-w:g0", "m-w:g1", "m-w:g2"]
    assert router.idle(now=7.5, idle_s=10) == [] and len(router.idle(now=100.0)) == 1
    router.process_all(ready)
    dets = [v for t, _, v in prod.sent if t == "detections"]
    decs = [v for t, _, v in prod.sent if t == "decisions"]
    assert len(decs) == 3 and len(dets) == 3 * 2 * 2          # 2 frames x 2 surfaces per group
    assert all(k == "m-w" for _, k, _ in prod.sent)
    assert {d.frame_id for d in dets if d.shot_id} >= {"m-w:0", "m-w:1"}
    # the non-representative frame inherits the representative's brand per surface
    by = {(d.frame_id, d.surface_type): d.brand_id for d in dets}
    assert by[("m-w:0", "led_perimeter")] == by[("m-w:1", "led_perimeter")]
    assert all(d.reason and d.budget_left_usd <= 3.0 for d in decs)
    traces = [r for t, r in ch.rows if t == "agent_traces"]
    assert traces and all(r["agent"] == "router" and r["model"].startswith("stub") for r in traces)


def test_detection_geometry_and_threshold():
    g = group(n=1)
    det = StubSurfaceDetector(sharpness=None).detect(g)
    out = route_group(g, [s.box for s in det], SYNTH_BRANDS, {}, TH, LocalBudget(1))
    dets = detections_for(g, det, out, min_area_pct=1.5, min_sharpness=50)
    led = next(d for d in dets if d.surface_type == "led_perimeter")
    assert led.area_pct == 2.2 and not led.below_threshold and 0 < led.center_dist < 1
    board = next(d for d in dets if d.surface_type == "static_board")
    assert board.area_pct == 0.9 and board.below_threshold
