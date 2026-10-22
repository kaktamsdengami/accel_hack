"""Pure aggregation logic for T10 (no I/O): detections -> intervals -> stats, coverage ledger.

Rules: docs/CONTRACTS.md sections 4, 5 and 7. T05's tables store the results; Valkey counters and DB
writes live elsewhere. Time is in seconds from the start of the match. Percentages are 0..100.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from app.contracts import (
    BrandStats,
    CoverageSecond,
    CoverageStatus,
    CoverageSummary,
    DecisionLog,
    Detection,
    Interval,
    LedSlot,
    PaceStatus,
    Shot,
    Side,
    SurfaceStats,
    SurfaceType,
    UnmeasuredReason,
)

PACE_AHEAD = 1.1   # projected >= 1.1 * target
PACE_BEHIND = 0.9  # projected < 0.9 * target
EPS = 1e-6

Span = tuple[float, float]


# --- small helpers -------------------------------------------------------------

def union_spans(spans: Iterable[Span]) -> list[Span]:
    """Merge overlapping or touching [start, end) spans."""
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + EPS:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def union_seconds(spans: Iterable[Span]) -> float:
    return round(sum(b - a for a, b in union_spans(spans)), 3)


def _spans(intervals: Iterable[Interval]) -> list[Span]:
    return [(i.t_start, i.t_end) for i in intervals]


def _pct(seconds: float, match_duration_s: float) -> float:
    return round(100.0 * seconds / match_duration_s, 1) if match_duration_s > 0 else 0.0


def _weighted_area(intervals: Sequence[Interval]) -> float:
    total = sum(i.duration_s for i in intervals)
    if total <= 0:
        return 0.0
    return round(sum(i.avg_area_pct * i.duration_s for i in intervals) / total, 2)


# --- 1. detections -> intervals --------------------------------------------------

def detections_to_intervals(
    detections: Iterable[Detection],
    shots: Iterable[Shot],
    sampled_fps: float,
    settings,
    gap_tolerance_samples: int = 1,
) -> list[Interval]:
    """Group credited detections into visibility intervals.

    A sample at time t represents [t, t + 1/sampled_fps). Consecutive samples of the same
    (brand, surface, side, board) inside one shot merge; up to `gap_tolerance_samples` missing samples
    in between are bridged. Intervals never cross a shot boundary. Detections that are
    below_threshold or have no brand are not credited. `settings` needs `closeup_area_pct`.
    """
    step = 1.0 / sampled_fps
    shot_by_id = {s.shot_id: s for s in shots}

    # key -> sample index -> detections at that sample
    groups: dict[tuple, dict[int, list[Detection]]] = defaultdict(lambda: defaultdict(list))
    for d in detections:
        if d.below_threshold or d.brand_id is None:
            continue
        key = (d.match_id, d.shot_id, d.brand_id, d.surface_type, d.side, d.board_id)
        groups[key][round(d.t * sampled_fps)].append(d)

    intervals: list[Interval] = []
    for (match_id, shot_id, brand_id, surface_type, side, board_id), samples in groups.items():
        shot = shot_by_id.get(shot_id)
        indexes = sorted(samples)
        runs: list[list[int]] = [[indexes[0]]]
        for idx in indexes[1:]:
            if idx - runs[-1][-1] - 1 <= gap_tolerance_samples:
                runs[-1].append(idx)
            else:
                runs.append([idx])
        for run in runs:
            # one value per sample: the largest box wins when a surface shows twice in one sample
            per_sample = [max(samples[i], key=lambda d: d.area_pct) for i in run]
            t_start = min(d.t for d in per_sample)
            t_end = max(d.t for d in per_sample) + step
            if shot is not None:
                t_end = min(t_end, shot.t_end)
            t_end = max(t_end, t_start)
            areas = [d.area_pct for d in per_sample]
            intervals.append(
                Interval(
                    match_id=match_id,
                    brand_id=brand_id,
                    surface_type=surface_type,
                    side=side,
                    board_id=board_id,
                    t_start=round(t_start, 3),
                    t_end=round(t_end, 3),
                    duration_s=round(t_end - t_start, 3),
                    avg_area_pct=round(sum(areas) / len(areas), 3),
                    max_area_pct=max(areas),
                    avg_center_dist=round(sum(d.center_dist for d in per_sample) / len(per_sample), 3),
                    is_closeup=max(areas) >= settings.closeup_area_pct,
                    is_replay=bool(shot.is_replay) if shot is not None else False,
                )
            )
    intervals.sort(key=lambda i: (i.t_start, i.brand_id, i.surface_type, i.side, i.board_id or ""))
    return intervals


# --- 2. per-brand stats and pace -------------------------------------------------

def brand_stats(
    intervals: Sequence[Interval],
    match_duration_s: float,
    contracts: Mapping[str, float] | None,
    clock_t: float,
    brand_names: Mapping[str, str] | None = None,
) -> list[BrandStats]:
    """Brand totals as unions over surfaces. `contracts` maps brand_id -> target seconds per match.

    `seconds` includes replays; `replay_seconds` is the replay part; pacing uses the non-replay union
    only: projected = seconds_excluding_replays / clock_t * match_duration_s.
    """
    contracts = contracts or {}
    names = brand_names or {}
    by_brand: dict[str, list[Interval]] = defaultdict(list)
    for i in intervals:
        by_brand[i.brand_id].append(i)
    for brand_id in contracts:
        by_brand.setdefault(brand_id, [])

    out: list[BrandStats] = []
    for brand_id, items in by_brand.items():
        seconds = union_seconds(_spans(items))
        live_seconds = union_seconds(_spans(i for i in items if not i.is_replay))
        target = contracts.get(brand_id)
        projected = None
        pace = PaceStatus.no_target
        if target is not None:
            if clock_t > 0:
                projected = round(live_seconds / clock_t * match_duration_s, 1)
                if projected >= PACE_AHEAD * target:
                    pace = PaceStatus.ahead
                elif projected < PACE_BEHIND * target:
                    pace = PaceStatus.behind
                else:
                    pace = PaceStatus.on_track
            else:
                pace = PaceStatus.on_track  # nothing played yet, nothing to judge
        out.append(
            BrandStats(
                brand_id=brand_id,
                brand_name=names.get(brand_id, brand_id),
                seconds=seconds,
                pct_of_match=_pct(seconds, match_duration_s),
                closeup_seconds=union_seconds(_spans(i for i in items if i.is_closeup)),
                replay_seconds=union_seconds(_spans(i for i in items if i.is_replay)),
                avg_area_pct=_weighted_area(items),
                surfaces=len({(i.surface_type, i.side, i.board_id) for i in items}),
                target_seconds=target,
                projected_seconds=projected,
                pace=pace,
            )
        )
    out.sort(key=lambda b: (-b.seconds, b.brand_id))
    return out


# --- 3. per-surface stats --------------------------------------------------------

def surface_label(surface_type: SurfaceType | str, side: Side | str = Side.unknown,
                  board_id: str | None = None) -> str:
    """Human label: "LED perimeter · far", "Board A · near", "Home shirt · chest".

    Shirts and backdrops carry no side. For a shirt, board_id may be "home:chest" -> "Home shirt · chest";
    any other board_id gives "Shirt · <board_id>".
    """
    st = SurfaceType(surface_type)
    sd = Side(side)
    side_part = "" if sd == Side.unknown else f" · {sd.value}"
    if st == SurfaceType.led_perimeter:
        return f"LED perimeter{side_part}"
    if st == SurfaceType.static_board:
        return (f"Board {board_id}" if board_id else "Board") + side_part
    if st == SurfaceType.shirt:
        if board_id and ":" in board_id:
            kit, place = board_id.split(":", 1)
            return f"{kit.capitalize()} shirt · {place}"
        return f"Shirt · {board_id}" if board_id else "Shirt"
    if st == SurfaceType.backdrop:
        return "Backdrop"
    if st == SurfaceType.banner:
        return "Banner" + side_part
    return "Other" + side_part


def surface_stats(
    intervals: Sequence[Interval],
    match_duration_s: float,
    brand_names: Mapping[str, str] | None = None,
) -> list[SurfaceStats]:
    """One row per (surface, brand). Each row counts its own union, so rows can sum above the brand total."""
    names = brand_names or {}
    groups: dict[tuple, list[Interval]] = defaultdict(list)
    for i in intervals:
        groups[(i.surface_type, i.side, i.board_id, i.brand_id)].append(i)
    out = []
    for (st, side, board_id, brand_id), items in groups.items():
        seconds = union_seconds(_spans(items))
        out.append(
            SurfaceStats(
                surface_label=surface_label(st, side, board_id),
                surface_type=st,
                side=side,
                board_id=board_id,
                brand_id=brand_id,
                brand_name=names.get(brand_id, brand_id),
                seconds=seconds,
                pct_of_match=_pct(seconds, match_duration_s),
                avg_area_pct=_weighted_area(items),
                closeup_seconds=union_seconds(_spans(i for i in items if i.is_closeup)),
            )
        )
    out.sort(key=lambda s: (s.surface_label, -s.seconds, s.brand_id))
    return out


# --- 4. LED rotation timeline ----------------------------------------------------

def led_timeline(
    intervals: Sequence[Interval], brand_names: Mapping[str, str] | None = None
) -> list[LedSlot]:
    """Which brand held each LED surface over time. Adjacent or overlapping same-brand slots merge."""
    names = brand_names or {}
    by_label: dict[str, list[Interval]] = defaultdict(list)
    for i in intervals:
        if i.surface_type == SurfaceType.led_perimeter:
            by_label[surface_label(i.surface_type, i.side, i.board_id)].append(i)
    slots: list[LedSlot] = []
    for label in sorted(by_label):
        merged: list[LedSlot] = []
        for i in sorted(by_label[label], key=lambda x: (x.t_start, x.brand_id)):
            for slot in reversed(merged):
                if slot.brand_id == i.brand_id and i.t_start <= slot.t_end + EPS:
                    slot.t_end = max(slot.t_end, i.t_end)
                    break
            else:
                merged.append(LedSlot(surface_label=label, brand_id=i.brand_id,
                                      brand_name=names.get(i.brand_id, i.brand_id),
                                      t_start=i.t_start, t_end=i.t_end))
        slots.extend(merged)
    slots.sort(key=lambda s: (s.surface_label, s.t_start))
    return slots


# --- 5. coverage ledger ----------------------------------------------------------

def coverage_ledger(
    match_id: str,
    duration_s: float,
    frames_t: Iterable[float],
    decisions: Iterable[DecisionLog],
    unmeasured_events: Iterable[tuple[float, UnmeasuredReason]],
) -> list[CoverageSecond]:
    """One CoverageSecond per whole second of the match (the last partial second counts as a second).

    A second is measured when it holds at least one sampled frame whose read succeeded:
    - no frame in the second -> unmeasured / no_frame;
    - a successful decision (confidence > 0) in the second -> measured, even if an event is also logged;
    - otherwise an unmeasured event (t, reason) in the second -> unmeasured with that reason
      (budget_cap, low_confidence, replay_excluded, reader_error); the first event logged wins;
    - a frame with no decision and no event is measured (read served by an earlier group decision).
    """
    total = math.ceil(duration_s)
    frame_seconds = {int(t) for t in frames_t}
    ok_seconds = {int(d.t) for d in decisions if d.confidence > 0}
    event_reason: dict[int, UnmeasuredReason] = {}
    for t, reason in unmeasured_events:
        event_reason.setdefault(int(t), UnmeasuredReason(reason))

    ledger = []
    for s in range(total):
        if s not in frame_seconds:
            status, reason = CoverageStatus.unmeasured, UnmeasuredReason.no_frame
        elif s in ok_seconds or s not in event_reason:
            status, reason = CoverageStatus.measured, None
        else:
            status, reason = CoverageStatus.unmeasured, event_reason[s]
        ledger.append(CoverageSecond(match_id=match_id, second=s, status=status, reason=reason))
    return ledger


def coverage_summary(ledger: Sequence[CoverageSecond]) -> CoverageSummary:
    measured = sum(1 for c in ledger if c.status == CoverageStatus.measured)
    by_reason: dict[UnmeasuredReason, float] = defaultdict(float)
    for c in ledger:
        if c.status == CoverageStatus.unmeasured and c.reason is not None:
            by_reason[UnmeasuredReason(c.reason)] += 1.0
    total = len(ledger)
    return CoverageSummary(
        measured_pct=round(100.0 * measured / total, 1) if total else 0.0,
        measured_seconds=float(measured),
        unmeasured_seconds=float(total - measured),
        by_reason=dict(by_reason),
    )
