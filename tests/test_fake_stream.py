from app.contracts import DecisionLog, Detection, Frame
from scripts.fake_stream import BRANDS, FAILED_GROUPS, MATCH_ID, TARGETS, generate, synthetic_match


def _all(**kw):
    frames, dets, decs = [], [], []
    for f, d, c in generate(**kw):
        frames.append(f)
        dets += d
        decs += c
    return frames, dets, decs


def test_synthetic_is_clearly_marked():
    m = synthetic_match()
    assert m.match_id == MATCH_ID == "m-synthetic" and m.source == "RECORDED"
    assert "SYNTHETIC" in m.title
    assert all(name.startswith("SYNTHETIC") for name in BRANDS.values())
    assert set(TARGETS) == set(BRANDS)


def test_generate_is_deterministic_and_valid():
    a = _all(duration_s=20)
    b = _all(duration_s=20)
    assert [d.model_dump() for d in a[1]] == [d.model_dump() for d in b[1]]
    frames, dets, decs = a
    assert len(frames) == 40 and frames[1].t == 0.5  # 2 fps
    assert all(isinstance(x, Frame) for x in frames)
    assert all(isinstance(x, Detection) for x in dets) and dets
    assert all(isinstance(x, DecisionLog) for x in decs)
    assert len({d.detection_id for d in dets}) == len(dets)
    assert len({d.decision_id for d in decs}) == len(decs) == 10  # one per 2 s window


def test_scenario_contents():
    _, dets, decs = _all(duration_s=60)
    assert {"led_perimeter", "static_board", "shirt"} <= {d.surface_type for d in dets}
    assert any(d.below_threshold for d in dets) and any(d.brand_id is None for d in dets)
    assert {"memory", "claude"} <= {d.chosen_reader for d in decs}
    failed = [d for d in decs if d.confidence == 0]
    assert len(failed) == len(FAILED_GROUPS)
    for d in failed:  # no detections inside a failed window
        assert not [x for x in dets if d.t <= x.t < d.t + 2]
    assert decs[-1].budget_left_usd < decs[0].budget_left_usd
