"""Pure aggregation logic for T10 (no I/O): detections -> intervals -> stats, coverage ledger.

Rules: docs/CONTRACTS.md sections 4, 5 and 7. T05's tables store the results; Valkey counters and DB
writes live elsewhere. Time is in seconds from the start of the match. Percentages are 0..100.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence

from app.contracts import (
    Alert,
    AlertKind,
    BrandStats,
    CostSummary,
    CoverageSecond,
    CoverageStatus,
    CoverageSummary,
    DecisionLog,
    Detection,
    Interval,
    LedSlot,
    Match,
    MatchReport,
    PaceStatus,
    Reader,
    Shot,
    SurfaceStats,
    SurfaceType,
    UnmeasuredReason,
)
from app.labels import surface_label  # re-exported: the label logic lives in app/labels.py

PACE_AHEAD = 1.1   # projected >= 1.1 * target
PACE_BEHIND = 0.9  # projected < 0.9 * target
PACE_ALERT_MIN_FRACTION = 0.1  # no under_pace alert before 10% of the match has been played
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


# --- 6. shots, unmeasured events, contracts, cost (inputs of the live report) ----------

def derive_shots(detections: Iterable[Detection], sampled_fps: float) -> list[Shot]:
    """Shots are not on Kafka, so rebuild them from detections: a shot spans its first to last sample.

    Camera/replay are unknown here (camera="main", is_replay=False). When the router publishes shot
    metadata, pass real `Shot` rows to `build_report(shots=...)` instead.
    """
    span: dict[tuple[str, str], list[float]] = {}
    for d in detections:
        s = span.setdefault((d.match_id, d.shot_id), [d.t, d.t])
        s[0], s[1] = min(s[0], d.t), max(s[1], d.t)
    step = 1.0 / sampled_fps
    return [Shot(shot_id=sid, match_id=mid, t_start=a, t_end=b + step, camera="main")
            for (mid, sid), (a, b) in span.items()]


def unmeasured_events(decisions: Iterable[DecisionLog]) -> list[tuple[float, UnmeasuredReason]]:
    """A decision with confidence 0 is a failed read. Its reason string names the cause when it starts
    with an UnmeasuredReason value ("budget_cap", "reader_error: timeout"); otherwise low_confidence."""
    out: list[tuple[float, UnmeasuredReason]] = []
    for d in sorted(decisions, key=lambda x: x.t):
        if d.confidence > 0:
            continue
        reason = next((r for r in UnmeasuredReason if d.reason.startswith(r.value)),
                      UnmeasuredReason.low_confidence)
        out.append((d.t, reason))
    return out


def contract_targets(rows: Iterable[tuple[str, str | None, float]]) -> dict[str, float]:
    """Postgres `contracts` rows (brand_id, surface_type, target_seconds) -> brand_id -> target seconds.

    A row with surface_type NULL is the brand-level target and wins; otherwise the surface rows are summed.
    """
    brand_level: dict[str, float] = {}
    surface_level: dict[str, float] = defaultdict(float)
    for brand_id, surface_type, seconds in rows:
        if surface_type is None:
            brand_level[brand_id] = float(seconds)
        else:
            surface_level[brand_id] += float(seconds)
    return {**surface_level, **brand_level}


def cost_summary(decisions: Sequence[DecisionLog], clock_t: float, budget_usd: float) -> CostSummary:
    """Spend from decision logs. Reader counts and Claude share use the last decision per group_id
    (same definition as eval.metrics.claude_call_share)."""
    total = round(sum(d.cost_usd for d in decisions), 6)
    final: dict[str, DecisionLog] = {}
    for d in sorted(decisions, key=lambda x: (x.t, x.decision_id)):
        final[d.group_id] = d
    calls: dict[Reader, int] = defaultdict(int)
    for d in final.values():
        calls[Reader(d.chosen_reader)] += 1
    claude = calls.get(Reader.claude, 0)
    latest = max(decisions, key=lambda x: (x.t, x.decision_id), default=None)
    left = latest.budget_left_usd if latest is not None else budget_usd
    minutes = clock_t / 60.0
    return CostSummary(
        usd_total=total,
        usd_per_match_minute=round(total / minutes, 6) if minutes > 0 else 0.0,
        claude_call_share=round(claude / len(final), 3) if final else 0.0,
        calls_by_reader=dict(calls),
        budget_usd=budget_usd,
        budget_left_usd=left,
    )


# --- 7. the full live report -------------------------------------------------------

def build_report(
    match: Match,
    detections: Sequence[Detection],
    decisions: Sequence[DecisionLog],
    frames_t: Iterable[float],
    settings,
    contracts: Mapping[str, float] | None = None,
    brand_names: Mapping[str, str] | None = None,
    shots: Sequence[Shot] | None = None,
) -> tuple[MatchReport, list[Interval], list[CoverageSecond]]:
    """Everything the live path needs from the data seen so far, as pure maths.

    - clock_t = end of the latest sample seen (detections, decisions, frames).
    - Percentages use match.duration_s, or the elapsed clock while the duration is unknown.
    - Coverage covers the elapsed seconds [0, ceil(clock_t)), not the unplayed rest of the match.
    - Pace and under_pace alerts need a known duration; without it brands carry no_target.
    - `settings` needs closeup_area_pct and match_budget_usd.
    Returns (report, intervals, coverage ledger) so the worker can store all three.
    """
    fps = match.sampled_fps
    step = 1.0 / fps
    times = [d.t for d in detections] + [d.t for d in decisions] + list(frames_t)
    frame_ts = list(times)
    clock_t = round(max(times) + step, 3) if times else 0.0
    duration = match.duration_s or clock_t
    shots = list(shots) if shots is not None else derive_shots(detections, fps)
    names = brand_names or {}
    targets = contracts if (match.duration_s and contracts) else None

    intervals = detections_to_intervals(detections, shots, fps, settings)
    brands = brand_stats(intervals, duration, targets, clock_t, names)
    surfaces = surface_stats(intervals, duration, names)
    ledger = coverage_ledger(match.match_id, clock_t, frame_ts, decisions, unmeasured_events(decisions))
    cost = cost_summary(decisions, clock_t, settings.match_budget_usd)

    alerts: list[Alert] = []
    if match.duration_s and clock_t >= PACE_ALERT_MIN_FRACTION * match.duration_s:
        for b in brands:
            if b.pace == PaceStatus.behind:
                alerts.append(Alert(
                    alert_id=f"a-{match.match_id}-under_pace-{b.brand_id}", match_id=match.match_id,
                    t=clock_t, kind=AlertKind.under_pace, brand_id=b.brand_id,
                    message=(f"{b.brand_name} is behind pace: projected {b.projected_seconds:.0f}s "
                             f"vs target {b.target_seconds:.0f}s")))
    if decisions and cost.budget_left_usd <= 0:
        alerts.append(Alert(alert_id=f"a-{match.match_id}-budget", match_id=match.match_id, t=clock_t,
                            kind=AlertKind.budget, message="Model budget for this match is used up"))

    report = MatchReport(
        match=match, clock_t=clock_t, brands=brands, surfaces=surfaces,
        led_timeline=led_timeline(intervals, names), coverage=coverage_summary(ledger),
        cost=cost, alerts=alerts)
    return report, intervals, ledger
