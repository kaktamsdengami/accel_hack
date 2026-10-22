"""T10 part 1: interval maths on small synthetic fixtures. Every expected number is a hand calculation.

All fixtures sample at 2 fps, so one sample is worth 0.5 s.
"""
from types import SimpleNamespace

import pytest

from app.config import settings as app_settings
from app.contracts import (
    BBox,
    CoverageStatus,
    DecisionLog,
    Detection,
    Interval,
    PaceStatus,
    Reader,
    Shot,
    UnmeasuredReason,
)
from workers.aggregate_logic import (
    brand_stats,
    coverage_ledger,
    coverage_summary,
    detections_to_intervals,
    led_timeline,
    surface_label,
    surface_stats,
    union_seconds,
)

FPS = 2.0
CFG = SimpleNamespace(closeup_area_pct=3.0)
SHOTS = [
    Shot(shot_id="s1", match_id="m", t_start=0, t_end=10, camera="main"),
    Shot(shot_id="s2", match_id="m", t_start=10, t_end=20, camera="main"),
    Shot(shot_id="r1", match_id="m", t_start=20, t_end=30, camera="replay", is_replay=True),
]


def det(t, brand="A", surface="led_perimeter", side="far", shot="s1", area=1.0, board=None,
        below=False, center=0.2):
    return Detection(
        detection_id=f"d-{brand}-{surface}-{side}-{t}", match_id="m", frame_id=f"f-{t}", shot_id=shot,
        t=t, bbox=BBox(x=0.1, y=0.1, w=0.1, h=0.1), surface_type=surface, side=side, board_id=board,
        brand_id=brand, confidence=0.9, reader=Reader.memory, area_pct=area, center_dist=center,
        below_threshold=below,
    )


def series(t0, n, **kw):
    """n consecutive samples starting at t0."""
    return [det(t0 + k / FPS, **kw) for k in range(n)]


def iv(brand, t0, t1, surface="led_perimeter", side="far", area=1.0, replay=False, closeup=False,
       board=None):
    return Interval(match_id="m", brand_id=brand, surface_type=surface, side=side, board_id=board,
                    t_start=t0, t_end=t1, duration_s=t1 - t0, avg_area_pct=area, max_area_pct=area,
                    avg_center_dist=0.2, is_closeup=closeup, is_replay=replay)


def run(dets, **kw):
    return detections_to_intervals(dets, SHOTS, FPS, CFG, **kw)


# --- detections -> intervals --------------------------------------------------

def test_consecutive_samples_merge():
    # samples at 0, 0.5, 1.0 cover [0, 1.5)
    (i,) = run(series(0, 3))
    assert (i.t_start, i.t_end, i.duration_s) == (0, 1.5, 1.5)


def test_single_missing_sample_is_bridged():
    # samples at 0, 0.5, 1.5, 2.0; 1.0 missing -> one interval [0, 2.5)
    dets = [det(t) for t in (0, 0.5, 1.5, 2.0)]
    (i,) = run(dets)
    assert (i.t_start, i.t_end) == (0, 2.5)


def test_two_missing_samples_are_not_bridged():
    # samples at 0, 0.5, 2.0; 1.0 and 1.5 missing -> [0, 1.0) and [2.0, 2.5)
    a, b = run([det(t) for t in (0, 0.5, 2.0)])
    assert (a.t_start, a.t_end) == (0, 1.0)
    assert (b.t_start, b.t_end) == (2.0, 2.5)


def test_gap_tolerance_is_configurable():
    dets = [det(t) for t in (0, 0.5, 2.0)]
    assert len(run(dets, gap_tolerance_samples=0)) == 2
    assert len(run(dets, gap_tolerance_samples=2)) == 1


def test_shot_boundary_is_not_crossed():
    # contiguous samples 9.0, 9.5 | 10.0, 10.5 straddle the s1/s2 cut at 10 s
    dets = series(9.0, 2, shot="s1") + series(10.0, 2, shot="s2")
    a, b = run(dets)
    assert (a.t_start, a.t_end) == (9.0, 10.0)
    assert (b.t_start, b.t_end) == (10.0, 11.0)


def test_interval_is_clipped_to_shot_end():
    # sample at 9.75 would reach 10.25, but shot s1 ends at 10
    (i,) = run([det(9.75)])
    assert i.t_end == 10.0


def test_below_threshold_and_unknown_brand_are_not_credited():
    dets = series(0, 2, below=True) + [det(5, brand=None)] + series(2, 2)
    (i,) = run(dets)
    assert (i.t_start, i.t_end) == (2, 3.0)


def test_below_threshold_sample_inside_run_counts_as_a_gap():
    # 0, 0.5 ok; 1.0 below threshold; 1.5 ok -> bridged, one interval [0, 2.0)
    dets = [det(0), det(0.5), det(1.0, below=True), det(1.5)]
    (i,) = run(dets)
    assert (i.t_start, i.t_end) == (0, 2.0)


def test_averages_closeup_and_replay():
    dets = [det(0, area=1.0, center=0.2), det(0.5, area=3.0, center=0.4)]
    (i,) = run(dets)
    assert i.avg_area_pct == 2.0 and i.max_area_pct == 3.0
    assert i.avg_center_dist == pytest.approx(0.3)
    assert i.is_closeup is True and i.is_replay is False
    (r,) = run([det(20, shot="r1", area=1.0)])
    assert r.is_replay is True and r.is_closeup is False


def test_different_surfaces_do_not_merge():
    dets = series(0, 2, surface="led_perimeter", side="far") + series(0, 2, surface="static_board",
                                                                      side="near", board="A")
    assert len(run(dets)) == 2


def test_default_settings_have_closeup_threshold():
    assert app_settings().closeup_area_pct == 3.0
    (i,) = detections_to_intervals([det(0, area=3.0)], SHOTS, FPS, app_settings())
    assert i.is_closeup


# --- brand and surface totals ---------------------------------------------------

def test_brand_counted_once_surfaces_counted_separately():
    # A on LED far [0, 4) and on Board A near [2, 6): overlap [2, 4)
    intervals = [iv("A", 0, 4), iv("A", 2, 6, surface="static_board", side="near", board="A")]
    (b,) = brand_stats(intervals, 100, {}, 100)
    assert b.seconds == 6.0  # union, not 8
    assert b.surfaces == 2
    assert b.pct_of_match == 6.0
    rows = surface_stats(intervals, 100)
    assert sorted(r.seconds for r in rows) == [4.0, 4.0]  # per surface: 4 + 4 = 8 > 6


def test_closeup_and_replay_seconds_are_unions():
    intervals = [
        iv("A", 0, 4, closeup=True),
        iv("A", 2, 6, surface="static_board", side="near", board="A", closeup=True),
        iv("A", 20, 23, replay=True),
        iv("A", 21, 22, replay=True, surface="shirt", side="unknown"),
    ]
    (b,) = brand_stats(intervals, 100, {}, 100)
    assert b.closeup_seconds == 6.0
    assert b.replay_seconds == 3.0
    assert b.seconds == 9.0  # [0,6) + [20,23)


def test_avg_area_is_duration_weighted():
    intervals = [iv("A", 0, 1, area=1.0), iv("A", 10, 13, area=3.0)]
    (b,) = brand_stats(intervals, 100, {}, 100)
    assert b.avg_area_pct == 2.5  # (1*1 + 3*3) / 4


def test_union_seconds_touching_spans():
    assert union_seconds([(0, 1), (1, 2), (5, 6)]) == 3.0


# --- pace -----------------------------------------------------------------------

@pytest.mark.parametrize(
    "seen_s, pace, projected",
    [
        (24, PaceStatus.ahead, 48.0),      # 24 / 50 * 100 = 48 >= 1.1 * 40 = 44
        (20, PaceStatus.on_track, 40.0),   # exactly on target
        (36, PaceStatus.ahead, 72.0),
        (17, PaceStatus.behind, 34.0),     # 34 < 0.9 * 40 = 36
        (18, PaceStatus.on_track, 36.0),   # 36 is not < 36
    ],
)
def test_pace_vs_target(seen_s, pace, projected):
    # 100 s match, clock at 50 s, target 40 s
    (b,) = brand_stats([iv("A", 0, seen_s)], 100, {"A": 40}, 50)
    assert b.pace == pace
    assert b.projected_seconds == projected
    assert b.target_seconds == 40


def test_pace_no_target():
    (b,) = brand_stats([iv("A", 0, 10)], 100, {}, 50)
    assert b.pace == PaceStatus.no_target
    assert b.projected_seconds is None and b.target_seconds is None


def test_pace_ignores_replays_and_brand_without_intervals_is_behind():
    # 20 s live + 30 s replay: projection uses 20 s only -> 40 s
    intervals = [iv("A", 0, 20), iv("A", 20, 50, replay=True)]
    stats = {b.brand_id: b for b in brand_stats(intervals, 100, {"A": 40, "B": 40}, 50)}
    assert stats["A"].projected_seconds == 40.0
    assert stats["A"].seconds == 50.0
    assert stats["B"].seconds == 0 and stats["B"].pace == PaceStatus.behind


def test_pace_before_kickoff_does_not_divide_by_zero():
    (b,) = brand_stats([], 100, {"A": 40}, 0)
    assert b.projected_seconds is None and b.pace == PaceStatus.on_track


# --- labels and LED timeline -----------------------------------------------------

def test_surface_labels():
    assert surface_label("led_perimeter", "far") == "LED perimeter · far"
    assert surface_label("static_board", "near", "A") == "Board A · near"
    assert surface_label("shirt", "unknown", "home:chest") == "Home shirt · chest"
    assert surface_label("backdrop", "left") == "Backdrop"


def test_surface_stats_rows_and_names():
    intervals = [iv("A", 0, 4), iv("B", 4, 6), iv("A", 0, 2, surface="static_board", side="near", board="A")]
    rows = surface_stats(intervals, 100, {"A": "Alpha"})
    labels = [(r.surface_label, r.brand_id, r.seconds, r.brand_name) for r in rows]
    assert labels == [
        ("Board A · near", "A", 2.0, "Alpha"),
        ("LED perimeter · far", "A", 4.0, "Alpha"),
        ("LED perimeter · far", "B", 2.0, "B"),
    ]


def test_led_timeline_merges_adjacent_same_brand_and_skips_other_surfaces():
    intervals = [
        iv("A", 0, 5), iv("A", 5, 10), iv("B", 10, 15), iv("A", 15, 20),
        iv("A", 0, 3, surface="static_board", side="near", board="A"),
        iv("C", 0, 5, side="near"),
    ]
    slots = led_timeline(intervals)
    far = [(s.brand_id, s.t_start, s.t_end) for s in slots if s.surface_label == "LED perimeter · far"]
    assert far == [("A", 0, 10), ("B", 10, 15), ("A", 15, 20)]
    assert {s.surface_label for s in slots} == {"LED perimeter · far", "LED perimeter · near"}


# --- coverage ---------------------------------------------------------------------

def decision(t, conf=0.9):
    return DecisionLog(decision_id=f"x{t}", match_id="m", shot_id="s1", group_id="g", t=t,
                       readers_tried=[Reader.memory], chosen_reader=Reader.memory, confidence=conf,
                       reason="test", budget_left_usd=1.0)


def test_coverage_ledger_sums_to_full_duration():
    # 10 s match, frames in seconds 0-2 and 5-9. Seconds 3, 4 have no frame.
    frames = [0, 0.5, 1, 1.5, 2, 2.5, 5, 5.5, 6, 7, 8, 9]
    decisions = [decision(0), decision(1), decision(2), decision(5.5), decision(6)]
    # second 7: low confidence; second 8: budget cap; second 9: replay (also a decision logged
    # in second 5 with an event, which must not hide the success)
    events = [(7.0, UnmeasuredReason.low_confidence), (8.0, UnmeasuredReason.budget_cap),
              (9.0, UnmeasuredReason.replay_excluded), (5.0, UnmeasuredReason.reader_error)]
    ledger = coverage_ledger("m", 10, frames, decisions, events)

    assert [c.second for c in ledger] == list(range(10))
    status = {c.second: (c.status, c.reason) for c in ledger}
    assert status[0] == (CoverageStatus.measured, None)
    assert status[3] == (CoverageStatus.unmeasured, UnmeasuredReason.no_frame)
    assert status[4] == (CoverageStatus.unmeasured, UnmeasuredReason.no_frame)
    assert status[5] == (CoverageStatus.measured, None)  # a successful read beats the logged error
    assert status[7] == (CoverageStatus.unmeasured, UnmeasuredReason.low_confidence)
    assert status[8] == (CoverageStatus.unmeasured, UnmeasuredReason.budget_cap)
    assert status[9] == (CoverageStatus.unmeasured, UnmeasuredReason.replay_excluded)

    s = coverage_summary(ledger)
    # measured: 0, 1, 2, 5, 6 -> 5 s of 10
    assert s.measured_seconds == 5 and s.unmeasured_seconds == 5
    assert s.measured_seconds + s.unmeasured_seconds == 10
    assert s.measured_pct == 50.0
    assert s.by_reason == {
        UnmeasuredReason.no_frame: 2, UnmeasuredReason.low_confidence: 1,
        UnmeasuredReason.budget_cap: 1, UnmeasuredReason.replay_excluded: 1,
    }
    assert sum(s.by_reason.values()) == s.unmeasured_seconds


def test_coverage_partial_last_second_counts_and_no_frames_at_all():
    ledger = coverage_ledger("m", 2.5, [], [], [])
    assert len(ledger) == 3
    s = coverage_summary(ledger)
    assert s.measured_pct == 0 and s.by_reason == {UnmeasuredReason.no_frame: 3}
